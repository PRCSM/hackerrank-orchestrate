"""Reconstruct each resource's state at audit_at, and infer its data category.

Each state dimension (presence, identifiability, attributability, data-flow
activity, restore access) is taken from the latest usable observation that speaks to it.
"Latest" orders by the time the fact was observed; at equal times evidence
files beat activity rows and explicit observations beat the absence implied
by a snapshot that does not list the resource, and a present copy beats an
absent one (a surviving replica row is still a copy). The observation that
decided a dimension is the evidence cited for it.
"""
from __future__ import annotations

from dataclasses import dataclass

from graph.evidence_graph import EvidenceGraph
from ingest.observation import Observation

PREFIX_CATEGORY = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "analytics": "ANALYTICS_PROFILE",
    "support": "SUPPORT_MESSAGE",
    "export": "USER_EXPORT",
    "data_flow": "DATA_FLOW",
    "backup": "BACKUP_ARCHIVE",
    "audit_record": "SECURITY_AUDIT_LOG",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "marketing": "MARKETING_PROFILE",
    "cache": "CACHE_ENTRY",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "object": "SUPPORT_ATTACHMENT",
    "legal_hold": "LEGAL_HOLD_RECORD",
}
# When observations disagree, the more specific evidence of what the record
# is wins: a contact/marketing record or an attachment is not a conversation.
HINT_PRIORITY = ("SECONDARY_IDENTIFIER", "MARKETING_PROFILE", "SUPPORT_ATTACHMENT", "SUPPORT_MESSAGE")


@dataclass(frozen=True)
class Fact:
    value: bool
    evidence: Observation


@dataclass
class ResourceState:
    resource_id: str
    system_id: str
    category: str
    presence: Fact | None
    identifiable: Fact | None
    flow_active: Fact | None
    restore_allowed: Fact | None
    attributable: Fact | None
    observations: list[Observation]

    @property
    def is_present(self) -> bool | None:
        return None if self.presence is None else self.presence.value


def _order(obs: Observation) -> tuple:
    return (obs.observed_at, obs.source_type == "file", not obs.implicit, obs.captured_at, obs.evidence_id)


def _latest(observations: list[Observation], dimension: str) -> Fact | None:
    relevant = [o for o in observations if getattr(o, dimension) is not None]
    if not relevant:
        return None
    newest_time = max(o.observed_at for o in relevant)
    newest = [o for o in relevant if o.observed_at == newest_time]
    if dimension == "presence" and any(o.presence for o in newest):
        newest = [o for o in newest if o.presence]
    chosen = max(newest, key=_order)
    return Fact(bool(getattr(chosen, dimension)), chosen)


def infer_category(resource_id: str, observations: list[Observation]) -> str:
    stated = {o.category for o in observations if o.category}
    for category in HINT_PRIORITY:
        if category in stated:
            return category
    if stated:
        return sorted(stated)[0]
    prefix = resource_id.rsplit("_", 1)[0]
    return PREFIX_CATEGORY.get(prefix, "UNKNOWN")


def resource_state_at(graph: EvidenceGraph, resource_id: str, system_id: str, audit_at: str) -> ResourceState:
    observations = graph.resource_observations(resource_id, audit_at)
    explicit_presence = [o for o in observations if o.presence is not None]
    if not explicit_presence:
        observations = observations + graph.absence_by_enumeration(resource_id, system_id, audit_at)
    return ResourceState(
        resource_id=resource_id,
        system_id=system_id,
        category=infer_category(resource_id, observations),
        presence=_latest(observations, "presence"),
        identifiable=_latest(observations, "identifiable"),
        flow_active=_latest(observations, "flow_active"),
        restore_allowed=_latest(observations, "restore_allowed"),
        attributable=_latest(observations, "attributable"),
        observations=observations,
    )
