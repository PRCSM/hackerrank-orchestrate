"""Log evidence in every supplied style.

Supported line shapes (after the leading ISO timestamp):
  key=value pairs ................ `level=INFO service=x trace_id=t event=e k=v`
  JSON objects ................... `{"service": "x", "trace_id": "t", ...}`
  bracketed ...................... `[INFO] [service] [trace] event: k="v"`
  pipe-delimited ................. `INFO | service | trace | event | k="v"`
  RFC-5424 syslog ................ `<15>1 node service trace - - k="v"`
  cloud runtime .................. `requestId=t level=INFO function=x message="k=\"v\""`

Lines are grouped by trace id. A "started" line usually carries the lookup key
(the identity used to find the record) and the "completed" line carries the
resource id and its readback state, so a trace is merged into one observation
per resource. Only identity keys flow from resource-less lines into the
merged record; their other fields (cache misses, retries, pool stats) describe
infrastructure, not the record.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ingest.manifest import EvidenceFile
from ingest.observation import candidate_keys, observation_from_fields
from ingest.parsed import ParsedFile

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
PAIR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
BRACKET = re.compile(r"^\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+(\w+):\s*(.*)$")
SYSLOG = re.compile(r"^<\d+>\d+\s+\S+\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")


@dataclass
class _Trace:
    shared_keys: set[str] = field(default_factory=set)
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    times: dict[str, str] = field(default_factory=dict)
    lines: dict[str, int] = field(default_factory=dict)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    return value


def parse_pairs(text: str) -> dict[str, str]:
    return {key: _unquote(value) for key, value in PAIR.findall(text)}


def parse_line(body: str) -> tuple[str | None, dict[str, Any]]:
    """Return (trace_id, fields) for one log line body; trace None if absent."""
    if body.startswith("{"):
        fields = json.loads(body)
        return fields.get("trace_id"), fields
    bracket = BRACKET.match(body)
    if bracket:
        level, service, trace, event, rest = bracket.groups()
        fields = parse_pairs(rest)
        fields.update(level=level, service=service, event=event)
        return trace, fields
    if " | " in body:
        parts = [p.strip() for p in body.split(" | ", 4)]
        if len(parts) == 5:
            level, service, trace, event, rest = parts
            fields = parse_pairs(rest)
            fields.update(level=level, service=service, event=event)
            return trace, fields
    syslog = SYSLOG.match(body)
    if syslog:
        service, trace, rest = syslog.groups()
        fields = parse_pairs(rest)
        fields["service"] = service
        return trace, fields
    fields = parse_pairs(body)
    if "message" in fields and "requestId" in fields:
        inner = parse_pairs(fields.pop("message"))
        fields.update(inner)
        return fields.get("requestId"), fields
    return fields.get("trace_id"), fields


def parse_logs(evidence: EvidenceFile) -> ParsedFile:
    parsed = ParsedFile(file_id=evidence.file_id)
    traces: dict[str, _Trace] = {}
    for number, raw in enumerate(evidence.path.read_text(encoding="utf-8").splitlines(), start=1):
        match = LINE.match(raw.strip())
        if not match:
            continue
        timestamp, body = match.groups()
        parsed.units_total += 1
        try:
            trace_id, fields = parse_line(body)
        except (ValueError, json.JSONDecodeError):
            parsed.units_unparsed += 1
            continue
        trace = traces.setdefault(trace_id or f"line-{number}", _Trace())
        resource = fields.get("resource_id")
        if resource and resource != "null":
            merged = trace.resources.setdefault(resource, {})
            events = " ".join(filter(None, (merged.get("event"), fields.get("event"))))
            merged.update(fields)
            if events:
                merged["event"] = events  # keep every step of the trace (e.g. contact_query + marker check)
            trace.times[resource] = timestamp
            trace.lines.setdefault(resource, number)
        else:
            for value in fields.values():
                trace.shared_keys |= candidate_keys(value)
    for trace_id, trace in traces.items():
        for resource, fields in trace.resources.items():
            fields = dict(fields)
            # Identity keys seen elsewhere in the trace (e.g. the lookup key of
            # the "started" line) under a neutral, non-identifier field name.
            for index, key in enumerate(sorted(trace.shared_keys)):
                fields[f"trace_key_{index}"] = key
            obs = observation_from_fields(
                fields,
                evidence_id=evidence.file_id,
                source_type="file",
                captured_at=evidence.captured_at,
                observed_at=trace.times[resource],
                raw_ref=f"{evidence.path.name}:L{trace.lines[resource]}:{trace_id}",
            )
            if obs is not None:
                parsed.observations.append(obs)
    return parsed

