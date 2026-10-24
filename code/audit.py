"""Deterministic audit pipeline: intent gate -> linkage -> state -> plan.

This is the baseline every case gets. The agent layer (agent/) may propose a
different resource set for cases this module flags as uncertain; such
proposals are reconciled against this baseline in validate/reconcile.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agent.intent_rules import classify
from graph.identity import IdentityResolver
from graph.linkage import CaseLinks, Link, Linker
from graph.state import ResourceState, resource_state_at
from ingest.loader import load_graph
from ingest.requests import DeletionRequest
from policy.planner import CasePlan, assess, build_plan, not_applicable
from policy.rules import Policy

HASH_KEY = re.compile(r"\b[0-9a-f]{64}\b")


@dataclass
class CaseResult:
    request: DeletionRequest
    plan: CasePlan
    links: CaseLinks | None
    states: list[ResourceState] = field(default_factory=list)
    intent_reason: str = ""
    uncertainty: list[str] = field(default_factory=list)


class Auditor:
    def __init__(self, dataset_dir: Path) -> None:
        self.graph, self.parsed_files = load_graph(dataset_dir)
        self.resolver = IdentityResolver(dataset_dir)
        self.policy = Policy(dataset_dir)
        self.linker = Linker(self.graph, self.resolver)

    def link(self, requests: list[DeletionRequest], authorized: dict[str, bool]) -> dict[str, CaseLinks]:
        """Linkage across all deletion requests at once (batch attribution needs
        every competing request)."""
        return self.linker.link_all([r for r in requests if authorized[r.case_id]], self._violates)

    def _violates(self, request: DeletionRequest, links: CaseLinks) -> bool:
        states = [resource_state_at(self.graph, l.resource_id, l.system_id, request.audit_at) for l in links.links.values()]
        return any(assess(s, self.policy).finding for s in states)

    def plan_case(self, request: DeletionRequest, links: CaseLinks, resource_links: list[Link] | None = None) -> CaseResult:
        chosen = resource_links if resource_links is not None else sorted(links.links.values(), key=lambda l: l.resource_id)
        states = [resource_state_at(self.graph, l.resource_id, l.system_id, request.audit_at) for l in chosen]
        accounts = {l.resource_id for l in chosen if l.system_id == "account_service"}
        plan = build_plan(request.case_id, states, self.policy, accounts)
        result = CaseResult(request, plan, links, states)
        result.uncertainty = self.uncertainty(result, chosen)
        return result

    @staticmethod
    def not_applicable(request: DeletionRequest, reason: str) -> CaseResult:
        return CaseResult(request, not_applicable(request.case_id), None, intent_reason=reason)

    def evidence_times(self) -> dict[str, str]:
        """Every citable id (file, activity, audit row) -> the time it existed."""
        times = dict(self.graph.file_captured_at)
        times.update({a.activity_id: a.event_time for a in self.graph.activities})
        times.update({c.audit_id: c.event_time for c in self.resolver.changes})
        return times

    def intents(self, requests: list[DeletionRequest]) -> dict[str, tuple[bool, str]]:
        return {r.case_id: classify(r.request_text) for r in requests}

    def uncertainty(self, result: CaseResult, chosen: list[Link]) -> list[str]:
        """Reasons the deterministic answer may be wrong (router input)."""
        reasons: list[str] = []
        links = result.links
        if links is not None and len(links.identities) != 1:
            reasons.append(f"identity resolved to {len(links.identities)} users")
        if any(l.uncertain for l in chosen):
            reasons.append("batch-inferred resource with an equally plausible rival request")
        if any(l.channel == "key" and HASH_KEY.search(l.reason) for l in chosen):
            reasons.append("resource linked only through a hashed identifier")
        if not any(l.system_id == "account_service" for l in chosen):
            reasons.append("no account resource found")
        for state in result.states:
            times = [o.observed_at for o in state.observations if o.presence is not None]
            if times:
                newest = max(times)
                values = {o.presence for o in state.observations if o.presence is not None and o.observed_at == newest}
                if len(values) > 1:
                    reasons.append(f"conflicting observations for {state.resource_id} at {newest}")
        if result.plan.status == "COMPLETE" and any(not s.evidence_id for s in result.plan.steps):
            reasons.append("COMPLETE verification without proving evidence")
        return reasons
