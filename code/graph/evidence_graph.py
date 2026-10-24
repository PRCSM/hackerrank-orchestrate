"""Index of every observation, queryable by resource and by identity key.

Every query takes the case's audit time as a hard cutoff: an observation is
usable only if both its evidence item was captured and the fact itself was
observed at or before `before`. Later evidence is invisible to the case.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ingest.observation import Observation
from ingest.parse_activity import Activity


def usable(obs: Observation, before: str) -> bool:
    return obs.captured_at <= before and obs.observed_at <= before


@dataclass
class EvidenceGraph:
    observations: list[Observation]
    activities: list[Activity]
    # file_id -> {system_id: snapshot time} for SQL table snapshots.
    enumerations: dict[str, dict[str, str]]
    file_captured_at: dict[str, str]
    recreation_sources: dict[str, set[str]]
    by_resource: dict[str, list[Observation]] = field(default_factory=lambda: defaultdict(list))
    by_key: dict[str, list[Observation]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        for obs in self.observations:
            self.by_resource[obs.resource_id].append(obs)
            for key in obs.keys:
                self.by_key[key].append(obs)

    def resource_observations(self, resource_id: str, before: str) -> list[Observation]:
        return [o for o in self.by_resource.get(resource_id, []) if usable(o, before)]

    def observations_for_keys(self, keys: frozenset[str], before: str) -> list[Observation]:
        seen: dict[int, Observation] = {}
        for key in keys:
            for obs in self.by_key.get(key, []):
                if usable(obs, before):
                    seen[id(obs)] = obs
        return list(seen.values())

    def absence_by_enumeration(self, resource_id: str, system_id: str, before: str) -> list[Observation]:
        """Synthetic ABSENT observations: SQL snapshots that enumerate the
        resource's system but do not contain the resource."""
        present_in = {o.evidence_id for o in self.by_resource.get(resource_id, [])}
        result = []
        for file_id, systems in self.enumerations.items():
            snapshot_at = systems.get(system_id)
            if snapshot_at is None or file_id in present_in:
                continue
            if self.file_captured_at[file_id] > before or snapshot_at > before:
                continue
            result.append(Observation(
                evidence_id=file_id, source_type="file", captured_at=self.file_captured_at[file_id],
                observed_at=snapshot_at, system_id=system_id, resource_id=resource_id,
                presence=False, implicit=True, raw_ref=f"{file_id}:not-enumerated",
                summary=f"{resource_id} not present in snapshot of {system_id}",
            ))
        return result
