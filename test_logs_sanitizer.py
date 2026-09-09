"""Тесты для logs_sanitizer.py."""

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import re
import sys

from logs_sanitizer import Rule, load_rules, sanitize, sanitize_json


def test_url_always_masked():
    assert sanitize("GET https://example.com/api", [], []) == "GET [URL-REDACTED]"
    assert sanitize("url=http://a.b:8080/path", [], []) == "url=[URL-REDACTED]"
    assert sanitize("no url here", [], []) == "no url here"


def test_url_captures_credentials():
    assert sanitize("https://user:pass@host.com/path", [], []) == "[URL-REDACTED]"


def test_url_in_json_preserves_structure():
    line = '  "self": "https://jira.example.org/rest/api/2/issue/1316648",'
    assert sanitize(line, [], []) == '  "self": "[URL-REDACTED]",'


def test_url_in_json_object_stays_parseable():
    import json
    obj = '{"id": "131", "self": "https://jira.example.org/issue/131", "name": "Нововведение"}'
    result = sanitize(obj, [], [])
    parsed = json.loads(result, strict=False)
    assert parsed["self"] == "[URL-REDACTED]"
    assert parsed["id"] == "131"
    assert parsed["name"] == "Нововведение"


def test_summary_with_escaped_quote_not_corrupted():
    import json
    rules, env = load_rules(str(Path(__file__).parent / "sanitize.yaml"))
    line = '            "summary": "Отчет \\"Орла\\"",'
    out = sanitize(line, rules, env)
    assert out == '            "summary": "[REDACTED]",'
    obj = '{"summary": "Разрешить вход без пароля \\"админ\\"", "key": "SCRUM-1"}'
    json.loads(sanitize(obj, rules, env, mask_keys=True), strict=False)


def test_json_keeps_keys_and_masks_body():
    import json
    obj = {
        "key": "SCRUM-1234",
        "fields": {
            "summary": "Кнопка не нажимается",
            "description": "Полное описание задачи",
            "status": {"name": "In Progress"},
            "customfield_21823": 5,
        },
    }
    out = sanitize_json(obj, [])
    assert out["key"] == "SCRUM-1234"
    assert out["fields"]["summary"] == "[REDACTED]"
    assert out["fields"]["description"] == "[REDACTED]"
    assert out["fields"]["status"] == {"name": "In Progress"}
    assert out["fields"]["customfield_21823"] == 5


def test_json_changelog_masks_body_keeps_status():
    changelog = [
        {"field": "status", "fromString": "To Do", "toString": "In Progress"},
        {"field": "description", "fromString": "Старое описание", "toString": "Новое описание"},
        {"field": "Story points", "fromString": "5", "toString": "8"},
        {"field": "summary", "fromString": "Старый заголовок", "toString": "Новый заголовок"},
    ]
    out = sanitize_json(changelog, [])
    assert out[0] == {"field": "status", "fromString": "To Do", "toString": "In Progress"}
    assert out[1]["fromString"] == "[REDACTED]"
    assert out[1]["toString"] == "[REDACTED]"
    assert out[2] == {"field": "Story points", "fromString": "5", "toString": "8"}
    assert out[3]["fromString"] == "[REDACTED]"
    assert out[3]["toString"] == "[REDACTED]"


def test_json_urls_masked():
    obj = {
        "self": "https://jira.example.org/rest/api/2/issue/123",
        "iconUrl": "https://jira.example.org/secure/viewavatar",
        "key": "SCRUM-9",
    }
    out = sanitize_json(obj, [])
    assert out["self"] == "[URL-REDACTED]"
    assert out["iconUrl"] == "[URL-REDACTED]"
    assert out["key"] == "SCRUM-9"


def test_json_masks_strings_in_lists_and_mid_string_urls():
    out = sanitize_json(
        ["Error: see https://x.y/z for details", "token=secret123"],
        ["secret123"],
    )
    assert out == ["Error: see [URL-REDACTED] for details", "token=[TOKEN-REDACTED]"]




def test_env_values_masked():
    line = "token=abc123 secret=xyz"
    result = sanitize(line, [], ["abc123", "xyz"])
    assert result == "token=[TOKEN-REDACTED] secret=[TOKEN-REDACTED]"


def test_env_values_non_regex():
    line = "auth with token=$ecure OK"
    result = sanitize(line, [], ["$ecure"])
    assert result == "auth with token=[TOKEN-REDACTED] OK"


def test_regex_patterns():
    rules = [Rule(re.compile(r"\bSECRET-\d+\b"), "[REDACTED]")]
    result = sanitize("task SECRET-42 done", rules, [])
    assert result == "task [REDACTED] done"


def test_regex_with_flags():
    rules = [Rule(re.compile(r"secret-\d+", re.IGNORECASE), "[X]")]
    result = sanitize("SECRET-1 and Secret-2", rules, [])
    assert result == "[X] and [X]"


def test_literal_patterns_loaded():
    yaml_content = """\
literal:
  - text: 'Astra Linux'
    replacement: '[REDACTED]'
  - text: 'astra'
    replacement: '[R]'
"""
    rules, env = _load_from_yaml(yaml_content)
    assert len(rules) == 2
    line = sanitize("Astra Linux and astra", rules, env)
    assert line == "[REDACTED] and [R]"


def test_literal_escapes_regex_chars():
    yaml_content = """\
literal:
  - text: '[ERROR]'
    replacement: '[OK]'
"""
    rules, env = _load_from_yaml(yaml_content)
    result = sanitize("[ERROR] in module", rules, env)
    assert result == "[OK] in module"


def test_patterns_and_literal_combined():
    yaml_content = """\
patterns:
  - pattern: '\\bISSUE-\\d+\\b'
    replacement: '[K]'
literal:
  - text: 'secret'
    replacement: '[S]'
"""
    rules, env = _load_from_yaml(yaml_content)
    result = sanitize("ISSUE-99 has secret data", rules, env)
    assert result == "[K] has [S] data"


def test_mask_keys_flag():
    result = sanitize("KEY-1 and TASK-2", [], [], mask_keys=True)
    assert result == "[KEY-REDACTED] and [KEY-REDACTED]"


def test_mask_keys_flag_off_by_default():
    result = sanitize("KEY-1 and TASK-2", [], [])
    assert result == "KEY-1 and TASK-2"


def test_env_keys_from_yaml():
    os.environ["_TEST_SANITIZER_TOKEN"] = "deadbeef"
    try:
        yaml_content = """\
env_keys:
  - _TEST_SANITIZER_TOKEN
"""
        rules, env = _load_from_yaml(yaml_content)
        assert "deadbeef" in env
        result = sanitize("auth with deadbeef ok", rules, env)
        assert "TOKEN-REDACTED" in result
    finally:
        del os.environ["_TEST_SANITIZER_TOKEN"]


def test_missing_env_key_ignored():
    yaml_content = """\
env_keys:
  - NONEXISTENT_VAR_12345
"""
    rules, env = _load_from_yaml(yaml_content)
    assert env == []


def test_empty_yaml():
    rules, env = _load_from_yaml("")
    assert rules == []
    assert env == []


def test_file_io():
    with TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.log"
        out = Path(tmp) / "out.log"
        inp.write_text("GET https://jira.example.com/api\n", encoding="utf-8")

        old_argv = sys.argv[:]
        try:
            from logs_sanitizer import main
            sys.argv = ["logs_sanitizer.py", str(inp), str(out)]
            main()
            result = out.read_text(encoding="utf-8")
            assert result == "GET [URL-REDACTED]\n"
        finally:
            sys.argv = old_argv


def test_file_io_with_mask_keys():
    with TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.log"
        out = Path(tmp) / "out.log"
        inp.write_text("task KEY-1 done\n", encoding="utf-8")

        old_argv = sys.argv[:]
        try:
            from logs_sanitizer import main
            sys.argv = ["logs_sanitizer.py", "--mask-keys", str(inp), str(out)]
            main()
            result = out.read_text(encoding="utf-8")
            assert result == "task [KEY-REDACTED] done\n"
        finally:
            sys.argv = old_argv


def test_url_masked_before_env():
    line = "https://user:tok123@host"
    result = sanitize(line, [], ["tok123"])
    assert result == "[URL-REDACTED]"


def _load_from_yaml(content: str) -> tuple[list[Rule], list[str]]:
    import yaml
    with TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "test.yaml"
        cfg.write_text(content, encoding="utf-8")
        return load_rules(str(cfg))


def run() -> None:
    tests = [
        test_url_always_masked,
        test_url_captures_credentials,
        test_url_in_json_preserves_structure,
        test_url_in_json_object_stays_parseable,
        test_summary_with_escaped_quote_not_corrupted,
        test_json_keeps_keys_and_masks_body,
        test_json_changelog_masks_body_keeps_status,
        test_json_urls_masked,
        test_json_masks_strings_in_lists_and_mid_string_urls,
        test_env_values_masked,
        test_env_values_non_regex,
        test_regex_patterns,
        test_regex_with_flags,
        test_literal_patterns_loaded,
        test_literal_escapes_regex_chars,
        test_patterns_and_literal_combined,
        test_mask_keys_flag,
        test_mask_keys_flag_off_by_default,
        test_env_keys_from_yaml,
        test_missing_env_key_ignored,
        test_empty_yaml,
        test_file_io,
        test_file_io_with_mask_keys,
        test_url_masked_before_env,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            print(f"FAIL {test.__name__}: {exc}")
            failed += 1
    if failed:
        print(f"\n{failed} test(s) FAILED")
        sys.exit(1)
    print(f"All {len(tests)} tests passed")


if __name__ == "__main__":
    run()
