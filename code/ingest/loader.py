"""Load every participant-facing evidence source into one EvidenceGraph."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from graph.evidence_graph import EvidenceGraph
from ingest.manifest import EvidenceFile, load_manifest
from ingest.parse_activity import activity_observations, load_activities, recreation_sources, resource_aliases
from ingest.parse_json import parse_json
from ingest.parse_logs import parse_logs
from ingest.parse_media import parse_media
from ingest.parse_sql import parse_sql
from ingest.parse_text import parse_text
from ingest.parsed import ParsedFile


def parse_file(evidence: EvidenceFile, aliases: dict[str, str]) -> ParsedFile:
    suffix = evidence.suffix
    if suffix == ".sql":
        return parse_sql(evidence)
    if suffix == ".json":
        return parse_json(evidence)
    if suffix == ".log":
        return parse_logs(evidence)
    if suffix == ".txt":
        return parse_text(evidence)
    if suffix in {".png", ".pdf"}:
        return parse_media(evidence, aliases)
    raise ValueError(f"unsupported evidence format: {evidence.path}")


def load_graph(dataset_dir: Path) -> tuple[EvidenceGraph, list[ParsedFile]]:
    manifest = load_manifest(dataset_dir)
    activities = load_activities(dataset_dir)
    aliases = resource_aliases(activities)
    parsed_files = [parse_file(evidence, aliases) for evidence in manifest]
    observations = [obs for parsed in parsed_files for obs in parsed.observations]
    for activity in activities:
        observations.extend(activity_observations(activity))
    graph = EvidenceGraph(
        observations=observations,
        activities=activities,
        enumerations={p.file_id: p.enumerated_systems for p in parsed_files if p.enumerated_systems},
        file_captured_at={e.file_id: e.captured_at for e in manifest},
        recreation_sources=recreation_sources(activities),
    )
    return graph, parsed_files


def coverage_report(graph: EvidenceGraph, parsed_files: list[ParsedFile]) -> str:
    """Human-readable ingest coverage: per file, per system, per source type."""
    lines = ["file_id      units  unparsed  observations"]
    for parsed in parsed_files:
        lines.append(f"{parsed.file_id:<12} {parsed.units_total:>5} {parsed.units_unparsed:>9} {len(parsed.observations):>13}")
    by_system = Counter(o.system_id for o in graph.observations)
    by_source = Counter(o.source_type for o in graph.observations)
    lines.append("")
    lines.append("observations per system: " + ", ".join(f"{k}={v}" for k, v in sorted(by_system.items())))
    lines.append("observations per source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    total_units = sum(p.units_total for p in parsed_files)
    unparsed = sum(p.units_unparsed for p in parsed_files)
    lines.append(f"units total={total_units} unparsed={unparsed} observations={len(graph.observations)}")
    return "\n".join(lines)
