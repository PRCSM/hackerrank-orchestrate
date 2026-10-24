"""Run every case: intent gate, linkage, deterministic plan, optional agent pass.

A failure in one case never stops the run: the case falls back to its
deterministic plan (or NOT_APPLICABLE if even intent failed) and the trace
records the error.
"""
from __future__ import annotations

import traceback
from collections import Counter
from pathlib import Path

from audit import Auditor, CaseResult
from ingest.requests import DeletionRequest
from storage.trace import case_trace, write_trace


class Orchestrator:
    def __init__(self, auditor: Auditor, *, use_llm: bool, resume: bool, trace_dir: Path) -> None:
        self.auditor = auditor
        self.use_llm = use_llm
        self.resume = resume
        self.trace_dir = trace_dir
        self.stats: Counter = Counter()

    def run(self, targets: list[DeletionRequest], all_requests: list[DeletionRequest]) -> list[CaseResult]:
        intents = self.auditor.intents(all_requests)
        authorized = {case: ok for case, (ok, _) in intents.items()}
        links = self.auditor.link(all_requests, authorized)
        results = []
        for request in targets:
            ok, reason = intents[request.case_id]
            try:
                if not ok:
                    result = self.auditor.not_applicable(request, reason)
                else:
                    result = self.auditor.plan_case(request, links[request.case_id])
                    result.intent_reason = reason
                extra = {"mode": "deterministic"}
            except Exception as error:  # one bad case must not kill the run
                self.stats["errors"] += 1
                result = self.auditor.not_applicable(request, f"error: {error}")
                extra = {"mode": "error", "error": traceback.format_exc(limit=3)}
            self.stats[result.plan.status] += 1
            if result.uncertainty:
                self.stats["uncertain"] += 1
            write_trace(case_trace(result, extra), self.trace_dir)
            results.append(result)
        return results

    def summary(self) -> str:
        return "run stats: " + ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items()))
