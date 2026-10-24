"""Tool-using investigator: re-examines one uncertain case's resource set.

The loop gives the model the case row, the router's uncertainty flags and the
baseline resource list, then lets it call the tools in agent/tools.py for at
most INVESTIGATOR_MAX_ITERATIONS turns. The final message must be the JSON
proposal described in prompts/investigator.md. Guardrails applied here:
- a proposed resource must have been returned by a tool in this conversation
  and its system_id must match its id prefix, otherwise it is dropped (logged);
- malformed JSON gets one repair turn, then the investigator abstains;
- the model never sets states or steps; code does (see validate/reconcile.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.llm_client import GroqClient
from agent.tools import TOOL_SCHEMAS, CaseTools
from config import INVESTIGATOR_MAX_ITERATIONS, MAX_OUTPUT_TOKENS, PROMPT_DIR
from ingest.observation import system_for_resource

PROMPT = (PROMPT_DIR / "investigator.md").read_text(encoding="utf-8")


@dataclass
class Proposal:
    abstain: bool
    resources: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    dropped: list[str] = field(default_factory=list)
    turns: int = 0
    model: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"abstain": self.abstain, "resources": self.resources, "summary": self.summary,
                "dropped": self.dropped, "turns": self.turns, "model": self.model}


def _case_message(tools: CaseTools, flags: list[str]) -> str:
    r = tools.request
    baseline = [{"resource_id": l.resource_id, "channel": l.channel, "uncertain": l.uncertain}
                for l in sorted(tools.links.links.values(), key=lambda l: l.resource_id)]
    return json.dumps({"case": {"case_id": r.case_id, "email": r.email, "requested_at": r.requested_at,
                                "reported_at": r.reported_at, "audit_at": r.audit_at},
                       "uncertainty_flags": flags, "baseline_resources": baseline}, ensure_ascii=False)


def _parse_final(content: str, tools: CaseTools) -> Proposal:
    payload = json.loads(content)
    proposal = Proposal(abstain=bool(payload.get("abstain", False)), summary=str(payload.get("summary", ""))[:200])
    for item in payload.get("resources", []) if not proposal.abstain else []:
        resource_id = str(item.get("resource_id", ""))
        system_id = str(item.get("system_id", ""))
        if resource_id not in tools.seen_resources:
            proposal.dropped.append(f"{resource_id}: never returned by a tool")
        elif system_for_resource(resource_id) != system_id:
            proposal.dropped.append(f"{resource_id}: system {system_id} does not match its id")
        else:
            proposal.resources.append({"resource_id": resource_id, "system_id": system_id,
                                       "linkage_reason": str(item.get("linkage_reason", ""))[:160]})
    return proposal


def investigate(client: GroqClient, model: str, tools: CaseTools, flags: list[str]) -> Proposal:
    messages: list[dict[str, Any]] = [{"role": "system", "content": PROMPT},
                                      {"role": "user", "content": _case_message(tools, flags)}]
    case_id = tools.request.case_id
    for turn in range(1, INVESTIGATOR_MAX_ITERATIONS + 1):
        last = turn == INVESTIGATOR_MAX_ITERATIONS
        reply = client.chat(model, messages, purpose="investigate", case_id=case_id,
                            tools=None if last else TOOL_SCHEMAS, json_mode=last, max_tokens=MAX_OUTPUT_TOKENS)
        message = reply.message
        messages.append(message)
        calls = message.get("tool_calls") or []
        if calls and not last:
            for call in calls:
                function = call.get("function", {})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": tools.call(function.get("name", ""), function.get("arguments", "{}"))})
            continue
        try:
            proposal = _parse_final(message.get("content") or "", tools)
        except (ValueError, json.JSONDecodeError) as error:
            messages.append({"role": "user", "content": f"Invalid JSON ({error}). Reply with only the JSON object."})
            repair = client.chat(model, messages, purpose="investigate-repair", case_id=case_id, json_mode=True,
                                 max_tokens=MAX_OUTPUT_TOKENS)
            try:
                proposal = _parse_final(repair.message.get("content") or "", tools)
            except (ValueError, json.JSONDecodeError):
                proposal = Proposal(abstain=True, summary="malformed JSON after repair")
        proposal.turns, proposal.model = turn, model
        return proposal
    return Proposal(abstain=True, summary="no final answer", turns=INVESTIGATOR_MAX_ITERATIONS, model=model)
