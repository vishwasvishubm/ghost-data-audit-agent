"""Normalize the observability-log line formats into one event shape.

The central observability exports wrap the same service events in different envelopes:

    logfmt     2026-.. level=INFO service=search_service trace_id=tr-1 event=x k=v
    json       2026-.. {"severity":"INFO","service":"search_service","trace_id":"tr-1","event":"x",...}
    syslog     2026-.. <15>1 node-1 account_service tr-1 - - level=DEBUG event="x" k="v"
    bracket    2026-.. [INFO] [account_service] [tr-1] x: k="v"
    cloud      2026-.. requestId=tr-1 level=INFO function=svc message="k=\\"v\\" event=\\"x\\""
    pipe       2026-.. | INFO | security_service | tr-1 | x | k="v"
"""

import json
import re
from dataclasses import dataclass

from .logfmt import convert, parse_line

_TS = r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
_SYSLOG = re.compile(_TS + r"\s+<\d+>\d+\s+\S+\s+(?P<svc>\S+)\s+(?P<trace>\S+)\s+-\s+-\s+(?P<rest>.*)$")
_BRACKET = re.compile(_TS + r"\s+\[(?P<lvl>\w+)\]\s+\[(?P<svc>[^\]]+)\]\s+\[(?P<trace>[^\]]+)\]\s+(?P<event>\w+):\s*(?P<rest>.*)$")
_PIPE = re.compile(_TS + r"\s+\|\s*(?P<lvl>\w+)\s*\|\s*(?P<svc>\S+)\s*\|\s*(?P<trace>\S+)\s*\|\s*(?P<event>\w+)\s*\|\s*(?P<rest>.*)$")
_JSON = re.compile(_TS + r"\s+(?P<rest>\{.*\})\s*$")
_CLOUD = re.compile(_TS + r'\s+requestId=(?P<trace>\S+)\s+level=(?P<lvl>\w+)\s+function=(?P<svc>\S+)\s+message="(?P<rest>.*)"\s*$')
_PAIRS = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


@dataclass
class LogEvent:
    ts: str
    service: str
    trace_id: str | None
    event: str
    fields: dict


def _pairs(text: str) -> dict:
    return {k: convert(v) for k, v in _PAIRS.findall(text)}


def parse_event(line: str) -> LogEvent | None:
    line = line.strip()
    if m := _CLOUD.match(line):
        fields = _pairs(m["rest"].replace('\\"', '"'))
        return LogEvent(m["ts"], m["svc"], m["trace"], str(fields.pop("event", "")), fields)
    if m := _SYSLOG.match(line):
        fields = _pairs(m["rest"])
        return LogEvent(m["ts"], m["svc"], m["trace"], str(fields.pop("event", "")), fields)
    if m := _BRACKET.match(line):
        return LogEvent(m["ts"], m["svc"], m["trace"], m["event"], _pairs(m["rest"]))
    if m := _PIPE.match(line):
        return LogEvent(m["ts"], m["svc"], m["trace"], m["event"], _pairs(m["rest"]))
    if m := _JSON.match(line):
        raw = json.loads(m["rest"])
        fields = {k: convert(str(v)) if isinstance(v, str) else v for k, v in raw.items()}
        return LogEvent(m["ts"], str(fields.pop("service", "")), fields.pop("trace_id", None), str(fields.pop("event", "")), fields)
    if parsed := parse_line(line):
        ts, fields = parsed
        return LogEvent(ts, str(fields.pop("service", "")), fields.pop("trace_id", None), str(fields.pop("event", "")), fields)
    return None
