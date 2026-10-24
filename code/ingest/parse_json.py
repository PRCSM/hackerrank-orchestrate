"""JSON evidence: snapshot bundles, service exports, ledgers and collections.

Store snapshots (`data_store` + `records`) also enumerate their store.

The JSON files share no single schema, so the parser walks the document and
treats every object that carries a `resource_id` as one record. The nearest
enclosing timestamp (observed_at / recorded_at / captured_at / exported_at)
becomes the observation time, and the enclosing collection or data store name
is kept as a category hint.
"""
from __future__ import annotations

import json
from typing import Any

from ingest.manifest import EvidenceFile
from ingest.observation import observation_from_fields
from ingest.parsed import ParsedFile, flatten_record

TIME_KEYS = ("observed_at", "recorded_at", "captured_at", "exported_at")
CONTEXT_KEYS = ("collection", "data_store")


def parse_json(evidence: EvidenceFile) -> ParsedFile:
    document = json.loads(evidence.path.read_text(encoding="utf-8"))
    parsed = ParsedFile(file_id=evidence.file_id)
    _walk(document, {"time": evidence.captured_at, "collection": None}, evidence, parsed)
    return parsed


def _walk(node: Any, context: dict[str, Any], evidence: EvidenceFile, parsed: ParsedFile) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, context, evidence, parsed)
        return
    if not isinstance(node, dict):
        return
    if "resource_id" in node:
        _record(node, context, evidence, parsed)
        return
    local = dict(context)
    for key in TIME_KEYS:
        if isinstance(node.get(key), str):
            local["time"] = node[key]
            break
    for key in CONTEXT_KEYS:
        if isinstance(node.get(key), str):
            local["collection"] = node[key]
    if isinstance(node.get("data_store"), str) and isinstance(node.get("records"), list):
        # A store snapshot lists that store's records of interest; a known
        # resource of the store missing from it is absent at snapshot time.
        parsed.enumerated_systems[node["data_store"]] = max(
            local["time"], parsed.enumerated_systems.get(node["data_store"], ""))
    for key, child in node.items():
        if isinstance(child, (dict, list)):
            # Service exports nest collections under their names.
            child_context = dict(local, collection=local["collection"] or key) if isinstance(child, list) else local
            _walk(child, child_context, evidence, parsed)


def _record(record: dict[str, Any], context: dict[str, Any], evidence: EvidenceFile, parsed: ParsedFile) -> None:
    parsed.units_total += 1
    fields = flatten_record(record)
    fields.setdefault("collection", context["collection"])
    if "status_code" not in fields and "result" not in fields:
        fields["__row__"] = True
    observed = next((record[k] for k in TIME_KEYS if isinstance(record.get(k), str)), context["time"])
    obs = observation_from_fields(
        fields,
        evidence_id=evidence.file_id,
        source_type="file",
        captured_at=evidence.captured_at,
        observed_at=observed,
        raw_ref=f"{evidence.path.name}:{record.get('resource_id')}",
    )
    if obs is None:
        parsed.units_unparsed += 1
    else:
        parsed.observations.append(obs)
