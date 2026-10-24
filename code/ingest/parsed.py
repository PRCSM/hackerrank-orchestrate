"""Container returned by every evidence parser, plus small shared helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ingest.observation import Observation

# Nested objects whose fields describe the record itself (merged upward).
MERGED_CHILDREN = frozenset({"document", "profile"})
# Nested business payloads that never carry identity or state.
IGNORED_CHILDREN = frozenset({"metrics", "recent_activity", "included_sections"})


@dataclass
class ParsedFile:
    """Observations from one file plus which systems it fully enumerates."""

    file_id: str
    observations: list[Observation] = field(default_factory=list)
    # system_id -> snapshot time; a resource of that system missing from the
    # file is absent at that time (used for SQL table snapshots only).
    enumerated_systems: dict[str, str] = field(default_factory=dict)
    units_total: int = 0
    units_unparsed: int = 0


def flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten one JSON record into the field names the semantics layer knows."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        if key in IGNORED_CHILDREN:
            continue
        if isinstance(value, dict) and key in MERGED_CHILDREN:
            flat.update({k: v for k, v in value.items() if not isinstance(v, (dict, list))})
            if key == "profile":
                flat["__row__"] = True
        elif isinstance(value, dict) and key == "hits":
            flat["total"] = value.get("total")
            for doc in value.get("documents", []):
                flat.update({k: v for k, v in doc.items() if k != "resource_id" and not isinstance(v, (dict, list))})
        elif not isinstance(value, (dict, list)):
            flat[key] = value
    return flat
