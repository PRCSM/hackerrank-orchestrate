"""Token usage ledger: one JSON line per model call (or cache hit)."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path


class UsageRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: list[dict] = []

    def record(self, *, model: str, purpose: str, case_id: str, input_tokens: int, output_tokens: int,
               cached: bool = False) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": model, "purpose": purpose,
            "case_id": case_id, "input_tokens": input_tokens, "output_tokens": output_tokens, "cached": cached,
        }
        self.records.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def totals(self) -> dict[str, dict[str, int]]:
        """model -> calls, cache_hits, input_tokens, output_tokens (this run)."""
        totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in self.records:
            t = totals[r["model"]]
            if r["cached"]:
                t["cache_hits"] += 1
                continue
            t["calls"] += 1
            t["input_tokens"] += r["input_tokens"]
            t["output_tokens"] += r["output_tokens"]
        return {model: dict(values) for model, values in totals.items()}
