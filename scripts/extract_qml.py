#!/usr/bin/env python3
"""Extract qsTr(...) strings from QML files into gettext .pot format."""

import re
import sys
from pathlib import Path

QSTR_RE = re.compile(r'qsTr\s*\(\s*"((?:[^"\\]|\\.)*)"')
QSTR_ARG_RE = re.compile(r'qsTr\s*\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def extract_qml(filepath: Path) -> list[tuple[str, int, str]]:
    results: list[tuple[str, int, str]] = []
    content = filepath.read_text(encoding="utf-8")
    for match in QSTR_RE.finditer(content):
        line_no = content[: match.start()].count("\n") + 1
        text = match.group(1).replace('\\"', '"')
        text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), text)
        results.append((filepath.name, line_no, text))
    return results


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: extract_qml.py <qml_file>...", file=sys.stderr)
        return 1

    entries: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for arg in sys.argv[1:]:
        fp = Path(arg)
        if not fp.exists():
            print(f"File not found: {fp}", file=sys.stderr)
            continue
        for filename, line_no, text in extract_qml(fp):
            key = (filename, text)
            entries.setdefault(key, []).append((filename, line_no))

    first = True
    for (filename, text), refs in entries.items():
        if not first:
            print()
        first = False
        for f, ln in refs:
            print(f"#: {f}:{ln}")
        print(f'msgctxt "{filename}"')
        print(f'msgid "{text}"')
        print('msgstr ""')

    return 0


if __name__ == "__main__":
    sys.exit(main())
