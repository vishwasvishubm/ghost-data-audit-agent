"""Shared parsing for logfmt-style lines: `<timestamp> key=value key="quoted value" ...`."""

import re

_LINE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(?P<rest>.*)$")
# A value runs until the next ` key=`, so unquoted values may contain spaces (e.g. phone numbers).
_PAIR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|.*?)(?=\s+\w+=|\s*$)')
_LITERALS = {"null": None, "true": True, "false": False}


def convert(value: str):
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]  # quoting is an envelope detail: "true" and true mean the same thing
    if value in _LITERALS:
        return _LITERALS[value]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_line(line: str) -> tuple[str, dict] | None:
    """Return (timestamp, fields) or None for lines that are not timestamped logfmt."""
    m = _LINE.match(line.strip())
    if not m:
        return None
    return m.group("ts"), {k: convert(v) for k, v in _PAIR.findall(m.group("rest"))}
