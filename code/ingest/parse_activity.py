"""system_activity.csv: claims, readback checks, recreation events and links.

`reported_result` is never treated as proof of state. Only rows whose details
describe an observed fact become state observations:
  CHECK_DATA_FLOW      "... confirmed that data flow X remains enabled"   -> flow active
  CHECK_RESTORE_ACCESS "... confirmed that archive X can still be restored" -> restore allowed
  SYNC_RECORD / RESTORE_BACKUP "... repopulated downstream resource R"   -> source active, R present
LINK_RESOURCE_IDENTITY rows link a resource (and an optional alias such as a
file name) to an email and user_id. Everything else is kept as an Activity for
deletion-batch reasoning and for intent corroboration.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from ingest.observation import EMAIL_PATTERN, Observation, candidate_keys, canonical_resource_id, system_for_resource

LINK_DETAILS = re.compile(r"(?:linked|confirmed)\s+(\S+)\s+\(([^)]+)\)")
REPOPULATED = re.compile(r"repopulated downstream resource\s+(\S+?)\.?$")
USER_IN_TEXT = re.compile(r"\buser_\d+\b")


@dataclass(frozen=True)
class Activity:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


def load_activities(dataset_dir: Path) -> list[Activity]:
    with open(dataset_dir / "system_activity.csv", newline="", encoding="utf-8") as handle:
        return [Activity(**row) for row in csv.DictReader(handle)]


def _obs(activity: Activity, resource_id: str, **facts: object) -> Observation:
    return Observation(
        evidence_id=activity.activity_id,
        source_type="activity",
        captured_at=activity.event_time,
        observed_at=activity.event_time,
        system_id=system_for_resource(resource_id),
        resource_id=resource_id,
        raw_ref=f"system_activity.csv:{activity.activity_id}",
        summary=activity.details[:400],
        **facts,
    )


def activity_observations(activity: Activity) -> list[Observation]:
    """State and link observations grounded in one activity row."""
    details = activity.details
    lowered = details.lower()
    if activity.event_type == "LINK_RESOURCE_IDENTITY":
        keys: set[str] = set()
        for email in EMAIL_PATTERN.findall(details):
            keys |= candidate_keys(email)
        keys |= set(USER_IN_TEXT.findall(details))
        return [_obs(activity, activity.resource_id, keys=frozenset(keys),
                     category="SUPPORT_ATTACHMENT" if "attachment" in lowered else None)]
    if activity.event_type == "CHECK_DATA_FLOW" and "confirmed" in lowered:
        return [_obs(activity, activity.resource_id, flow_active="remains enabled" in lowered or "still enabled" in lowered)]
    if activity.event_type == "CHECK_RESTORE_ACCESS" and "confirmed" in lowered:
        return [_obs(activity, activity.resource_id, restore_allowed="can still be restored" in lowered)]
    repopulated = REPOPULATED.search(details)
    if repopulated and activity.event_type in {"SYNC_RECORD", "RESTORE_BACKUP"}:
        downstream = canonical_resource_id(repopulated.group(1))
        source_fact = {"flow_active": True} if activity.event_type == "SYNC_RECORD" else {"restore_allowed": True}
        observations = [_obs(activity, activity.resource_id, **source_fact)]
        if downstream:
            observations.append(_obs(activity, downstream, presence=True))
        return observations
    return []


def resource_aliases(activities: list[Activity]) -> dict[str, str]:
    """Alias text -> resource id, from LINK_RESOURCE_IDENTITY details."""
    aliases: dict[str, str] = {}
    for activity in activities:
        if activity.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        match = LINK_DETAILS.search(activity.details)
        if match and match.group(2) != activity.resource_id:
            alias = re.sub(r"\.[a-z]{2,4}$", "", match.group(2))
            aliases[alias] = activity.resource_id
    return aliases


def recreation_sources(activities: list[Activity]) -> dict[str, set[str]]:
    """Downstream resource -> data flows / backups seen repopulating it."""
    sources: dict[str, set[str]] = {}
    for activity in activities:
        match = REPOPULATED.search(activity.details)
        downstream = canonical_resource_id(match.group(1)) if match else None
        if downstream:
            sources.setdefault(downstream, set()).add(activity.resource_id)
    return sources
