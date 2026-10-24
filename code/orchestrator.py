"""Run every case: intent gate, linkage, deterministic plan, optional agent pass.

LLM mode (GROQ_API_KEY set, no --no-llm):
1. intent: target requests are classified by the LLM in batches (agent/intent.py);
2. every case gets the deterministic baseline plan (audit.py);
3. router: cases whose baseline carries uncertainty flags are ranked by flag
   severity and the top MAX_INVESTIGATIONS go to the investigator;
4. each proposal is reconciled with the baseline (validate/reconcile.py) and
   re-planned by the deterministic planner; if the result fails validation the
   baseline stands.
Investigations are cached per case + prompt + baseline, so a rerun costs zero
tokens for finished cases. A daily-quota 429 switches to the fallback model,
then to deterministic mode for the rest of the run; those cases are listed.
A failure in one case never stops the run: the case falls back to its
deterministic plan (or NOT_APPLICABLE if even intent failed) and the trace
records the error.
"""
from __future__ import annotations

import json
import traceback
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from audit import Auditor, CaseResult
from config import CACHE_DIR, FALLBACK_MODEL, INVESTIGATOR_MODEL, MAX_INVESTIGATIONS, PRICE_PER_MTOK
from ingest.requests import DeletionRequest, case_sort_key
from storage.cache import ResultCache
from storage.trace import case_trace, write_trace
from storage.usage import UsageRecorder
from validate.reconcile import reconcile
from validate.validator import validate_case

if TYPE_CHECKING:
    from agent.llm_client import GroqClient

FLAG_SEVERITY = (
    ("identity resolved to", 3),
    ("no account resource found", 3),
    ("COMPLETE verification without", 2),
    ("conflicting observations", 2),
    ("hashed identifier", 1),
    ("batch-inferred", 1),
)


def flag_score(flags: list[str]) -> int:
    return sum(score for needle, score in FLAG_SEVERITY if any(needle in f for f in flags))


class Orchestrator:
    def __init__(self, auditor: Auditor, *, client: "GroqClient | None", resume: bool, trace_dir: Path,
                 cache_dir: Path = CACHE_DIR) -> None:
        self.auditor = auditor
        self.client = client
        self.trace_dir = trace_dir
        self.cache = ResultCache(cache_dir / "llm", enabled=resume)
        self.usage = client.usage if client is not None else UsageRecorder(cache_dir / "usage.jsonl")
        self.stats: Counter = Counter()
        self.log: list[str] = []

    # ------------------------------------------------------------------ intent
    def _intents(self, targets: list[DeletionRequest], all_requests: list[DeletionRequest]) -> dict[str, tuple[bool, str]]:
        intents = self.auditor.intents(all_requests)
        if self.client is not None:
            from agent.intent import classify_requests

            for case_id, decision in classify_requests(targets, self.client, self.cache, self.log).items():
                intents[case_id] = (decision.authorizes_deletion, decision.reason)
                self.stats[f"intent_{decision.source}"] += 1
        return intents

    # ------------------------------------------------------------------ run
    def run(self, targets: list[DeletionRequest], all_requests: list[DeletionRequest]) -> list[CaseResult]:
        intents = self._intents(targets, all_requests)
        authorized = {case: ok for case, (ok, _) in intents.items()}
        links = self.auditor.link(all_requests, authorized)
        results: dict[str, CaseResult] = {}
        extras: dict[str, dict] = {}
        for request in targets:
            ok, reason = intents[request.case_id]
            try:
                if ok:
                    result = self.auditor.plan_case(request, links[request.case_id])
                    result.intent_reason = reason
                else:
                    result = self.auditor.not_applicable(request, reason)
                extras[request.case_id] = {"mode": "deterministic"}
            except Exception as error:  # one bad case must not kill the run
                self.stats["errors"] += 1
                result = self.auditor.not_applicable(request, f"error: {error}")
                extras[request.case_id] = {"mode": "error", "error": traceback.format_exc(limit=3)}
            results[request.case_id] = result
        if self.client is not None:
            self._investigate(results, extras)
        else:
            self._route(results)  # deterministic mode: report what would be routed
        for request in targets:
            result = results[request.case_id]
            self.stats[result.plan.status] += 1
            if result.uncertainty:
                self.stats["uncertain"] += 1
            write_trace(case_trace(result, extras[request.case_id]), self.trace_dir)
        return [results[r.case_id] for r in targets]

    # ------------------------------------------------------------------ agent
    def _route(self, results: dict[str, CaseResult]) -> list[CaseResult]:
        flagged = [r for r in results.values() if r.uncertainty and r.links is not None]
        flagged.sort(key=lambda r: (-flag_score(r.uncertainty), case_sort_key(r.request.case_id)))
        chosen = flagged[:MAX_INVESTIGATIONS]
        self.stats["routed_flagged"] = len(flagged)
        self.stats["routed_investigated" if self.client is not None else "routed_within_budget"] = len(chosen)
        return chosen

    def _investigate(self, results: dict[str, CaseResult], extras: dict[str, dict]) -> None:
        from agent.investigator import PROMPT, investigate
        from agent.llm_client import LLMUnavailable, QuotaExhausted
        from agent.tools import CaseTools

        evidence_time = self.auditor.evidence_times()
        models = [INVESTIGATOR_MODEL, FALLBACK_MODEL]
        for result in self._route(results):
            request, case_id = result.request, result.request.case_id
            baseline_ids = sorted(result.links.links)
            key = ResultCache.key("investigate", case_id, PROMPT, INVESTIGATOR_MODEL, json.dumps(baseline_ids), *result.uncertainty)
            proposal = self.cache.get(key)
            extra = extras[case_id]
            extra["router_flags"] = result.uncertainty
            if proposal is not None:
                self.usage.record(model=INVESTIGATOR_MODEL, purpose="investigate", case_id=case_id,
                                  input_tokens=0, output_tokens=0, cached=True)
                self.stats["investigate_cache_hits"] += 1
            while proposal is None and models:
                tools = CaseTools(self.auditor, request, result.links)
                try:
                    proposal = investigate(self.client, models[0], tools, result.uncertainty).to_json()
                    self.cache.put(key, proposal)
                except QuotaExhausted as error:
                    self.log.append(f"quota exhausted on {models[0]} at {case_id}: {error}")
                    models.pop(0)
                except (LLMUnavailable, ValueError) as error:
                    self.log.append(f"investigator failed on {case_id}: {error}")
                    break
            if proposal is None:
                extra["mode"] = "deterministic (agent unavailable)"
                self.stats["investigate_skipped"] += 1
                continue
            links, decisions = reconcile(self.auditor, request, result.links, proposal)
            extra.update({"mode": "agent+reconcile", "proposal": proposal, "reconcile": decisions})
            if proposal.get("dropped"):
                self.stats["agent_ungrounded_dropped"] += len(proposal["dropped"])
            candidate = self.auditor.plan_case(request, result.links, links)
            candidate.intent_reason = result.intent_reason
            categories = {a.resource_id: a.category for a in candidate.plan.assessments}
            problems = validate_case(request, candidate.plan.status, candidate.plan.numbered(), evidence_time, categories)
            if problems:
                extra["reconcile"].append(f"reconciled plan invalid {problems}; baseline kept")
                continue
            if candidate.plan.steps != result.plan.steps:
                self.stats["agent_changed_plan"] += 1
            results[case_id] = candidate

    def summary(self) -> str:
        lines = ["run stats: " + ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items()))]
        for model, totals in sorted(self.usage.totals().items()):
            lines.append(f"usage {model}: " + ", ".join(f"{k}={v}" for k, v in sorted(totals.items())))
        lines.append(f"projected paid-tier cost: ${self.usage.projected_cost(PRICE_PER_MTOK):.4f} (actual on free tier: $0)")
        lines += self.log[:20]
        return "\n".join(lines)
