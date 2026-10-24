"""Reconcile an investigator proposal with the deterministic baseline.

Agreement -> accept. On disagreement the deterministic baseline wins unless
the agent's alternative is grounded in pre-cutoff evidence the baseline also
recognises as a legitimate candidate:

- a baseline resource is removed only if it was a batch inference flagged
  uncertain AND the agent proposes a same-type replacement;
- an agent-only resource is added only if it is one of this request's own
  deletion-batch candidates (it was in the batch window and unowned) — i.e. the
  agent resolved a tie the baseline broke arbitrarily — or it carries one of the
  subject's identifiers in a usable observation;
- everything else the agent proposes is rejected.

Every decision is returned for the case trace.
"""
from __future__ import annotations

from audit import Auditor
from graph.linkage import CaseLinks, Link, resource_type
from ingest.requests import DeletionRequest


def reconcile(auditor: Auditor, request: DeletionRequest, baseline: CaseLinks,
              proposal: dict) -> tuple[list[Link], list[str]]:
    base = dict(baseline.links)
    if proposal.get("abstain") or not proposal.get("resources"):
        return sorted(base.values(), key=lambda l: l.resource_id), ["agent abstained: baseline kept"]
    proposed = {r["resource_id"]: r for r in proposal["resources"]}
    decisions: list[str] = []
    candidates = {row["resource_id"] for row in auditor.linker.batch_candidates(request.case_id)}
    keys = frozenset().union(*(i.keys for i in baseline.identities)) if baseline.identities else frozenset()
    keyed = {o.resource_id for o in auditor.graph.observations_for_keys(keys, request.audit_at)}
    result = dict(base)
    for resource_id, item in sorted(proposed.items()):
        if resource_id in base:
            continue
        same_type = [r for r in base if resource_type(r) == resource_type(resource_id)]
        if resource_id in candidates:
            replaced = [r for r in same_type if base[r].channel == "batch" and base[r].uncertain and r not in proposed]
            if same_type and not replaced:
                decisions.append(f"rejected {resource_id}: baseline already has a confident {resource_type(resource_id)}")
                continue
            for old in replaced:
                result.pop(old, None)
                decisions.append(f"replaced uncertain batch link {old} with {resource_id}: {item['linkage_reason']}")
            result[resource_id] = Link(resource_id, item["system_id"], "agent",
                                       f"agent: {item['linkage_reason']}", uncertain=True)
            if not replaced:
                decisions.append(f"added batch candidate {resource_id}: {item['linkage_reason']}")
        elif resource_id in keyed:
            result[resource_id] = Link(resource_id, item["system_id"], "agent", f"agent: {item['linkage_reason']}")
            decisions.append(f"added identifier-linked {resource_id}")
        else:
            decisions.append(f"rejected {resource_id}: not a batch candidate and no identifier link before audit_at")
    for resource_id, link in sorted(base.items()):
        if resource_id not in proposed and resource_id in result:
            decisions.append(f"kept baseline {resource_id} the agent omitted ({link.channel})")
    if not decisions:
        decisions.append("agent agreed with baseline")
    return sorted(result.values(), key=lambda l: l.resource_id), decisions
