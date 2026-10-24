"""Plain-text handover records: `- time | system | resource | type | result | subject`."""
from __future__ import annotations

from ingest.manifest import EvidenceFile
from ingest.observation import observation_from_fields
from ingest.parsed import ParsedFile

COLUMNS = ("recorded_at", "system", "resource_id", "record_type", "result", "subject_reference")


def parse_text(evidence: EvidenceFile) -> ParsedFile:
    parsed = ParsedFile(file_id=evidence.file_id)
    for number, line in enumerate(evidence.path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("- ") or " | " not in line:
            continue
        parsed.units_total += 1
        parts = [p.strip() for p in line[2:].split(" | ")]
        if len(parts) != len(COLUMNS):
            parsed.units_unparsed += 1
            continue
        fields = dict(zip(COLUMNS, parts))
        obs = observation_from_fields(
            fields,
            evidence_id=evidence.file_id,
            source_type="file",
            captured_at=evidence.captured_at,
            observed_at=fields["recorded_at"],
            raw_ref=f"{evidence.path.name}:L{number}",
        )
        if obs is None:
            parsed.units_unparsed += 1
        else:
            parsed.observations.append(obs)
    return parsed
