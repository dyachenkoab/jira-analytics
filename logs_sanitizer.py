#!/usr/bin/env python3
"""Универсальный санитайзер логов. YAML-конфиг, URL-маскировка, env-токены.

Использование:
    python3 logs_sanitizer.py [--config FILE] [--mask-keys] [input] [output]
    python3 logs_sanitizer.py [--mask-keys] < input.log > clean.log
    python3 logs_sanitizer.py --test
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_URL = re.compile(r"https?://[^\s\"'<>]+")
_KEY = re.compile(r"\b[A-Z]+-\d+\b")
_SENSITIVE_FIELDS = {"summary", "description"}
_URL_PREFIXES = ("http://", "https://")

CONFIG_CWD = "sanitize.yaml"
CONFIG_HOME = Path.home() / ".config" / "logs-sanitizer.yaml"


@dataclass
class Rule:
    regex: re.Pattern
    replacement: str


def _find_config(config_arg: str | None) -> str | None:
    if config_arg:
        return config_arg
    if Path(CONFIG_CWD).exists():
        return CONFIG_CWD
    if CONFIG_HOME.exists():
        return str(CONFIG_HOME)
    return None


def load_rules(config_path: str | None) -> tuple[list[Rule], list[str]]:
    """Возвращает (rules, env_values).
    rules — скомпилированные regex-правила (включая literal).
    env_values — значения env-переменных для literal-замены.
    """
    rules: list[Rule] = []
    env_values: list[str] = []

    if config_path is None:
        return rules, env_values

    if yaml is None:
        print("PyYAML not installed. Install with: pip install PyYAML", file=sys.stderr)
        sys.exit(1)

    try:
        raw = Path(config_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Cannot read config {config_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        cfg = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        print(f"Invalid YAML in {config_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    for entry in cfg.get("patterns") or []:
        pat = entry.get("pattern", "")
        repl = entry.get("replacement", "[REDACTED]")
        flags_str = entry.get("flags", "") or ""
        flags = 0
        if "i" in flags_str:
            flags |= re.IGNORECASE
        if "m" in flags_str:
            flags |= re.MULTILINE
        rules.append(Rule(re.compile(pat, flags), repl))

    for entry in cfg.get("literal") or []:
        text = entry.get("text", "")
        repl = entry.get("replacement", "[REDACTED]")
        if text:
            rules.append(Rule(re.compile(re.escape(text)), repl))

    for key in cfg.get("env_keys") or []:
        val = os.environ.get(str(key), "")
        if val:
            env_values.append(val)

    return rules, env_values


def sanitize(line: str, rules: list[Rule], env_values: list[str], mask_keys: bool = False) -> str:
    line = _URL.sub("[URL-REDACTED]", line)
    for val in env_values:
        line = line.replace(val, "[TOKEN-REDACTED]")
    for rule in rules:
        line = rule.regex.sub(rule.replacement, line)
    if mask_keys:
        line = _KEY.sub("[KEY-REDACTED]", line)
    return line


def _mask_string(s: str, env_values: list[str]) -> str:
    s = _URL.sub("[URL-REDACTED]", s)
    for tok in env_values:
        s = s.replace(tok, "[TOKEN-REDACTED]")
    return s


def sanitize_json(value: Any, env_values: list[str]) -> Any:
    """Структурная санитизация JSON: маскирует summary/description, URL и тело
    в fromString/toString (changelog), но сохраняет ключи задач, статусы и SP."""
    if isinstance(value, dict):
        mask_body = value.get("field") in _SENSITIVE_FIELDS
        result: dict[str, Any] = {}
        for k, v in value.items():
            if k in _SENSITIVE_FIELDS and isinstance(v, str):
                result[k] = "[REDACTED]"
            elif k in ("fromString", "toString") and mask_body and isinstance(v, str):
                result[k] = "[REDACTED]"
            elif isinstance(v, str):
                result[k] = _mask_string(v, env_values)
            else:
                result[k] = sanitize_json(v, env_values)
        return result
    if isinstance(value, list):
        return [sanitize_json(x, env_values) for x in value]
    if isinstance(value, str):
        return _mask_string(value, env_values)
    return value


def _usage() -> None:
    print("Usage: python3 logs_sanitizer.py [--config FILE] [--mask-keys] [input] [output]", file=sys.stderr)
    print("       python3 logs_sanitizer.py [--mask-keys] < input > output", file=sys.stderr)
    print("       python3 logs_sanitizer.py --json [--config FILE] [input] [output]", file=sys.stderr)
    print("       python3 logs_sanitizer.py --test", file=sys.stderr)


def main() -> None:
    if any(a in sys.argv for a in ("--test", "-t", "--self-test")):
        import test_logs_sanitizer
        test_logs_sanitizer.run()
        return
    if any(a in sys.argv for a in ("-h", "--help")):
        _usage()
        sys.exit(0)

    config_arg = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--config":
            if i + 1 < len(argv):
                config_arg = argv[i + 1]
            else:
                print("Missing config path after --config", file=sys.stderr)
                sys.exit(1)

    args = [a for a in argv if a != "--config" and (config_arg is None or a != config_arg) and not a.startswith("-")]
    mask_keys = "--mask-keys" in sys.argv

    config_path = _find_config(config_arg)
    if config_arg and not Path(config_arg).exists():
        print(f"Config not found: {config_arg}", file=sys.stderr)
        sys.exit(1)

    rules, env_values = load_rules(config_path)

    if args:
        infile = Path(args[0])
        if not infile.exists():
            print(f"File not found: {infile}", file=sys.stderr)
            sys.exit(1)
        input_stream: Any = open(infile, encoding="utf-8")
    elif sys.stdin.isatty():
        _usage()
        sys.exit(1)
    else:
        input_stream = sys.stdin

    output_stream: Any = open(args[1], "w", encoding="utf-8") if len(args) >= 2 else sys.stdout

    try:
        if "--json" in sys.argv:
            import json as _json
            data = _json.loads(input_stream.read(), strict=False)
            _json.dump(sanitize_json(data, env_values), output_stream, ensure_ascii=False, indent=2)
        else:
            for line in input_stream:
                output_stream.write(sanitize(line, rules, env_values, mask_keys))
    finally:
        if input_stream is not sys.stdin:
            input_stream.close()
        if output_stream is not sys.stdout:
            output_stream.close()


if __name__ == "__main__":
    main()
