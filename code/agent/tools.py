"""Tools the investigator may call, wrapping graph / identity / state / policy.

Every tool is bound to one case and therefore to its audit_at cutoff: nothing
observed later can leak into the agent's context. Results are compact JSON,
truncated to TOOL_RESULT_CHAR_LIMIT characters, and every resource or
evidence id a tool returns is remembered so the final proposal can be checked
for grounding.
"""
from __future__ import annotations

import json
import re
from typing import Any

from audit import Auditor
from config import TOOL_RESULT_CHAR_LIMIT
from graph.linkage import CaseLinks
from graph.state import Fact, resource_state_at
from ingest.observation import RESOURCE_ID_PATTERN, system_for_resource
from ingest.requests import DeletionRequest

ID_IN_TEXT = re.compile(r"\b(?:evidence|activity|audit)_\d+\b")


def _schema(name: str, description: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    params = {"type": "object", "properties": properties or {}, "required": list((properties or {}).keys())}
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


RESOURCE_ARG = {"resource_id": {"type": "string", "description": "e.g. account_123"}}
TOOL_SCHEMAS = [
    _schema("resolve_identity", "Subject's user_id(s) and identifiers (current and historical) as of audit_at."),
    _schema("find_resources", "Resources linked to the subject, with linkage channel and reason."),
    _schema("batch_candidates", "Deletion-worker batch rows around reported_at that this request claims, with rivals."),
    _schema("resource_state", "State of one resource at audit_at and the evidence ids that prove it.", RESOURCE_ARG),
    _schema("resource_activity", "Activity rows (claims, not proof) for one resource up to audit_at.", RESOURCE_ARG),
    _schema("get_rule", "Deletion rule for a data category.", {"category": {"type": "string"}}),
]


class CaseTools:
    def __init__(self, auditor: Auditor, request: DeletionRequest, links: CaseLinks) -> None:
        self.auditor = auditor
        self.request = request
        self.links = links
        self.seen_resources: set[str] = set()
        self.seen_evidence: set[str] = set()

    def call(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments or "{}")
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                result: Any = {"error": f"unknown tool {name}"}
            else:
                result = handler(**args)
        except (TypeError, ValueError) as error:
            result = {"error": f"bad arguments: {error}"}
        text = json.dumps(result, sort_keys=True, ensure_ascii=False)[:TOOL_RESULT_CHAR_LIMIT]
        self.seen_resources |= {f"{m.group(1)}_{m.group(2)}" for m in RESOURCE_ID_PATTERN.finditer(text)}
        self.seen_evidence |= set(ID_IN_TEXT.findall(text))
        return text

    def _tool_resolve_identity(self) -> Any:
        return [{"user_id": i.user_id, "emails": sorted(i.emails), "phones": len(i.phones), "hashes": len(i.hashes)}
                for i in self.links.identities]

    def _tool_find_resources(self) -> Any:
        return [{"resource_id": l.resource_id, "system_id": l.system_id, "channel": l.channel,
                 "reason": l.reason, "uncertain": l.uncertain} for l in self.links.links.values()]

    def _tool_batch_candidates(self) -> Any:
        return self.auditor.linker.batch_candidates(self.request.case_id)

    def _tool_resource_state(self, resource_id: str) -> Any:
        if not RESOURCE_ID_PATTERN.fullmatch(resource_id):
            return {"error": "not a resource id"}
        state = resource_state_at(self.auditor.graph, resource_id, system_for_resource(resource_id), self.request.audit_at)

        def fact(f: Fact | None) -> Any:
            return None if f is None else {"value": f.value, "evidence_id": f.evidence.evidence_id, "observed_at": f.evidence.observed_at}

        return {"resource_id": resource_id, "category": state.category, "present": fact(state.presence),
                "identifiable": fact(state.identifiable), "attributable": fact(state.attributable),
                "flow_active": fact(state.flow_active), "restore_allowed": fact(state.restore_allowed),
                "observations": [o.summary[:160] for o in state.observations[-3:]]}

    def _tool_resource_activity(self, resource_id: str) -> Any:
        rows = [a for a in self.auditor.graph.activities if a.resource_id == resource_id and a.event_time <= self.request.audit_at]
        return [{"activity_id": a.activity_id, "time": a.event_time, "type": a.event_type, "claimed": a.reported_result,
                 "details": a.details[:120]} for a in rows[-8:]]

    def _tool_get_rule(self, category: str) -> Any:
        rule = self.auditor.policy.rule_for(category)
        return {"category": category, "required_action": self.auditor.policy.required_action(category),
                "condition": rule.condition if rule else "operation table"}
