"""SQLite-compatible snapshot exports: execute, then read every table row.

Each row becomes an observation of a present record. Primary and replica
tables are read alike, so a row that survives only in a read/regional/
reporting/failover replica (replication drift) is still a present copy. The
tables a file contains also tell us which systems it enumerates, so that an
absent row can later prove that a known resource no longer exists.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ingest.manifest import EvidenceFile
from ingest.observation import observation_from_fields
from ingest.parsed import ParsedFile, flatten_record

TABLE_SYSTEMS: tuple[tuple[str, str], ...] = (
    ("customer_profiles", "account_service"),
    ("search_documents", "search_service"),
    ("analytics", "analytics_platform"),
    ("support_records", "support_platform"),
    ("customer_exports", "export_service"),
    ("connector_subscriptions", "integration_service"),
    ("backup_restore_entries", "backup_service"),
    ("security_audit_records", "security_service"),
)
CAPTURED_HEADER = re.compile(r"Captured at:\s*(\S+)")


def _system_of_table(table: str) -> str | None:
    for needle, system in TABLE_SYSTEMS:
        if needle in table:
            return system
    return None


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f'SELECT * FROM "{table}"')
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def parse_sql(evidence: EvidenceFile) -> ParsedFile:
    script = evidence.path.read_text(encoding="utf-8")
    header = CAPTURED_HEADER.search(script)
    snapshot_at = header.group(1) if header else evidence.captured_at
    parsed = ParsedFile(file_id=evidence.file_id)
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(script)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        scope: dict[str, str] = {}
        if "snapshot_scope" in tables:
            scope = {row["origin_system"]: row["captured_at"] for row in _rows(conn, "snapshot_scope")}
        for table in tables:
            if table == "snapshot_scope":
                continue
            table_system = _system_of_table(table)
            if table_system:
                parsed.enumerated_systems.setdefault(table_system, scope.get(table_system, snapshot_at))
            for row in _rows(conn, table):
                parsed.units_total += 1
                fields = _row_fields(row)
                fields["__table__"] = table
                system = fields.get("origin_system") or table_system
                observed = scope.get(str(system), snapshot_at)
                obs = observation_from_fields(
                    fields,
                    evidence_id=evidence.file_id,
                    source_type="file",
                    captured_at=evidence.captured_at,
                    observed_at=observed,
                    raw_ref=f"{evidence.path.name}:{table}:{row.get('resource_id')}",
                )
                if obs is None:
                    parsed.units_unparsed += 1
                    continue
                parsed.observations.append(obs)
                if fields.get("origin_system"):
                    parsed.enumerated_systems.setdefault(obs.system_id, observed)
    finally:
        conn.close()
    return parsed


def _row_fields(row: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {k: v for k, v in row.items() if k != "record_json"}
    if row.get("record_json"):
        fields.update(flatten_record(json.loads(row["record_json"])))
        fields["collection"] = row.get("collection_name")
    for flag in ("enabled", "restore_allowed"):
        if isinstance(fields.get(flag), int):
            fields[flag] = bool(fields[flag])
    fields["__row__"] = True
    return fields
