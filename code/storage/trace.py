"""Per-case trace: why every step exists (identity, links, states, evidence)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from audit import CaseResult
from graph.state import Fact


def _fact(fact: Fact | None) -> dict[str, Any] | None:
    if fact is None:
        return None
    o = fact.evidence
    return {"value": fact.value, "evidence_id": o.evidence_id, "observed_at": o.observed_at,
            "implicit": o.implicit, "source": o.raw_ref}


def case_trace(result: CaseResult, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    request = result.request
    trace: dict[str, Any] = {
        "case_id": request.case_id,
        "audit_at": request.audit_at,
        "reported_status": request.reported_status,
        "intent": result.intent_reason,
        "status": result.plan.status,
        "uncertainty": result.uncertainty,
    }
    if result.links is not None:
        trace["identities"] = [
            {"user_id": i.user_id, "emails": sorted(i.emails), "hash_count": len(i.hashes), "audit_ids": i.audit_ids}
            for i in result.links.identities
        ]
        trace["links"] = [
            {"resource_id": l.resource_id, "system_id": l.system_id, "channel": l.channel,
             "reason": l.reason, "uncertain": l.uncertain}
            for l in sorted(result.links.links.values(), key=lambda l: l.resource_id)
        ]
    states = {s.resource_id: s for s in result.states}
    trace["assessments"] = [
        {
            "resource_id": a.resource_id, "system_id": a.system_id, "category": a.category,
            "requirement": a.requirement, "compliant": a.compliant, "note": a.note,
            "presence": _fact(states[a.resource_id].presence),
            "identifiable": _fact(states[a.resource_id].identifiable),
            "attributable": _fact(states[a.resource_id].attributable),
            "flow_active": _fact(states[a.resource_id].flow_active),
            "restore_allowed": _fact(states[a.resource_id].restore_allowed),
        }
        for a in result.plan.assessments
    ]
    trace["steps"] = result.plan.numbered()
    if extra:
        trace.update(extra)
    return trace


def write_trace(trace: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{trace['case_id']}.json").write_text(json.dumps(trace, indent=2, sort_keys=True), encoding="utf-8")
