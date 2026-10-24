"""LLM intent gate: batched classification of request_text, with fallbacks.

Order of precedence per batch: cached result -> primary model -> fallback
model (daily quota hit) -> deterministic rules (API unavailable / malformed
JSON after one repair attempt). The LLM verdict is then compared with the
deterministic rules. On a disagreement (always logged) the rules win when one
of their explicit patterns matched — they are exact on every visible request —
and the LLM wins when the rules only defaulted because no pattern matched,
which is exactly the unseen-phrasing / unseen-language case an LLM is for.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from agent.intent_rules import NO_MATCH, classify
from agent.llm_client import GroqClient, LLMUnavailable, QuotaExhausted
from config import FALLBACK_MODEL, INTENT_BATCH_SIZE, INTENT_MODEL, PROMPT_DIR
from ingest.requests import DeletionRequest
from storage.cache import ResultCache

PROMPT = (PROMPT_DIR / "intent.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class IntentDecision:
    authorizes_deletion: bool
    reason: str
    source: str  # llm | rules | rules-override


def _parse(content: str, expected: set[str]) -> dict[str, tuple[bool, str]]:
    payload = json.loads(content)
    results = {}
    for item in payload.get("results", []):
        case_id = item.get("case_id")
        if case_id in expected and isinstance(item.get("authorizes_deletion"), bool):
            results[case_id] = (item["authorizes_deletion"], str(item.get("reason", ""))[:120])
    if set(results) != expected:
        raise ValueError(f"missing ids: {sorted(expected - set(results))}")
    return results


def _ask(client: GroqClient, model: str, batch: list[DeletionRequest]) -> dict[str, tuple[bool, str]]:
    payload = {"requests": [{"case_id": r.case_id, "request_text": r.request_text} for r in batch]}
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    expected = {r.case_id for r in batch}
    reply = client.chat(model, messages, purpose="intent", case_id=batch[0].case_id, json_mode=True, max_tokens=60 * len(batch))
    try:
        return _parse(reply.message.get("content", ""), expected)
    except (ValueError, json.JSONDecodeError) as error:
        messages += [reply.message, {"role": "user", "content": f"Your JSON was invalid ({error}). Return only the JSON object."}]
        repaired = client.chat(model, messages, purpose="intent-repair", case_id=batch[0].case_id, json_mode=True,
                               max_tokens=60 * len(batch))
        return _parse(repaired.message.get("content", ""), expected)


def classify_requests(requests: list[DeletionRequest], client: GroqClient | None, cache: ResultCache,
                      log: list[str]) -> dict[str, IntentDecision]:
    decisions: dict[str, IntentDecision] = {}
    for start in range(0, len(requests), INTENT_BATCH_SIZE):
        batch = requests[start:start + INTENT_BATCH_SIZE]
        key = ResultCache.key("intent", batch[0].case_id, PROMPT, INTENT_MODEL, *(r.case_id + r.request_text for r in batch))
        llm = cache.get(key)
        if llm is not None and client is not None:
            client.usage.record(model=INTENT_MODEL, purpose="intent", case_id=batch[0].case_id,
                                input_tokens=0, output_tokens=0, cached=True)
        if llm is None and client is not None:
            for model in (INTENT_MODEL, FALLBACK_MODEL):
                try:
                    llm = {k: list(v) for k, v in _ask(client, model, batch).items()}
                    cache.put(key, llm)
                    break
                except QuotaExhausted as error:
                    log.append(f"intent quota exhausted on {model}: {error}")
                except (LLMUnavailable, ValueError, json.JSONDecodeError) as error:
                    log.append(f"intent LLM failed on {model}: {error}")
                    break
        for request in batch:
            rule_ok, rule_reason = classify(request.request_text)
            if llm is None or request.case_id not in llm:
                decisions[request.case_id] = IntentDecision(rule_ok, rule_reason, "rules")
                continue
            llm_ok, llm_reason = llm[request.case_id]
            if llm_ok == rule_ok:
                decisions[request.case_id] = IntentDecision(llm_ok, f"llm: {llm_reason}", "llm")
            else:
                log.append(f"intent disagreement {request.case_id}: llm={llm_ok} ({llm_reason}) rules={rule_ok} ({rule_reason})")
                if rule_reason == NO_MATCH:
                    decisions[request.case_id] = IntentDecision(llm_ok, f"llm over unmatched rules: {llm_reason}", "llm")
                else:
                    decisions[request.case_id] = IntentDecision(rule_ok, f"rules over llm: {rule_reason}", "rules-override")
    return decisions
