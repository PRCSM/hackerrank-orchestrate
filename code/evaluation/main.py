"""Score predictions against labelled answers (sample_output.csv or custom cases).

Metrics
- status accuracy and a status confusion matrix;
- step-level precision / recall / F1 over the multiset of
  (phase, system_id, resource_id, operation, state, evidence_id) tuples;
- the same without evidence_id (did we find the right work at all?);
- field-level accuracy: per field, share of gold steps whose predicted step at
  the same position has the same value;
- exact sequence match: the whole steps array is identical.

Usage: python3 code/evaluation/main.py --pred PRED.csv [--gold dataset/sample_output.csv] [--diff]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DATASET_DIR  # noqa: E402
from ingest.requests import case_sort_key  # noqa: E402
from storage.writer import read_output  # noqa: E402

FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")


def _tuples(steps: list[dict], with_evidence: bool = True) -> Counter:
    fields = FIELDS if with_evidence else FIELDS[:-1]
    return Counter(tuple(s.get(f) for f in fields) for s in steps)


def _prf(pred: Counter, gold: Counter) -> tuple[float, float, float]:
    hit = sum((pred & gold).values())
    precision = hit / sum(pred.values()) if pred else (1.0 if not gold else 0.0)
    recall = hit / sum(gold.values()) if gold else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def score(pred: dict[str, tuple[str, list[dict]]], gold: dict[str, tuple[str, list[dict]]]) -> dict:
    cases = sorted(gold, key=case_sort_key)
    confusion = Counter()
    pred_all, gold_all, pred_noev, gold_noev = Counter(), Counter(), Counter(), Counter()
    field_hits = Counter()
    gold_steps = 0
    exact = 0
    per_case = []
    for case_id in cases:
        g_status, g_steps = gold[case_id]
        p_status, p_steps = pred.get(case_id, ("MISSING", []))
        confusion[(g_status, p_status)] += 1
        pt, gt = _tuples(p_steps), _tuples(g_steps)
        pred_all += pt
        gold_all += gt
        pred_noev += _tuples(p_steps, False)
        gold_noev += _tuples(g_steps, False)
        for index, g_step in enumerate(g_steps):
            gold_steps += 1
            p_step = p_steps[index] if index < len(p_steps) else {}
            for f in FIELDS:
                field_hits[f] += p_step.get(f) == g_step.get(f)
        same = p_status == g_status and [tuple(s.get(f) for f in FIELDS) for s in p_steps] == [
            tuple(s.get(f) for f in FIELDS) for s in g_steps]
        exact += same
        if not same:
            per_case.append({
                "case_id": case_id, "gold_status": g_status, "pred_status": p_status,
                "missing": sorted((gt - pt).elements()), "extra": sorted((pt - gt).elements()),
            })
    p, r, f = _prf(pred_all, gold_all)
    p2, r2, f2 = _prf(pred_noev, gold_noev)
    return {
        "cases": len(cases),
        "status_accuracy": sum(v for (g, q), v in confusion.items() if g == q) / len(cases),
        "confusion": confusion,
        "step_precision": p, "step_recall": r, "step_f1": f,
        "step_precision_no_evidence": p2, "step_recall_no_evidence": r2, "step_f1_no_evidence": f2,
        "field_accuracy": {f_: field_hits[f_] / gold_steps for f_ in FIELDS} if gold_steps else {},
        "sequence_accuracy": exact / len(cases),
        "exact_cases": exact,
        "failures": per_case,
    }


def format_report(metrics: dict, title: str, diff: bool = False) -> str:
    lines = [f"### {title}", "", "| metric | value |", "|---|---|"]
    lines.append(f"| cases | {metrics['cases']} |")
    lines.append(f"| status accuracy | {metrics['status_accuracy']:.1%} |")
    lines.append(f"| step precision / recall / F1 | {metrics['step_precision']:.1%} / {metrics['step_recall']:.1%} / {metrics['step_f1']:.1%} |")
    lines.append(f"| step P / R / F1 ignoring evidence_id | {metrics['step_precision_no_evidence']:.1%} / "
                 f"{metrics['step_recall_no_evidence']:.1%} / {metrics['step_f1_no_evidence']:.1%} |")
    lines.append(f"| sequence (exact case) accuracy | {metrics['sequence_accuracy']:.1%} ({metrics['exact_cases']}/{metrics['cases']}) |")
    for name, value in metrics["field_accuracy"].items():
        lines.append(f"| field accuracy: {name} | {value:.1%} |")
    lines += ["", "Status confusion (rows = gold, columns = predicted):", "",
              "| gold \\ pred | " + " | ".join(STATUSES) + " |", "|---|" + "---|" * len(STATUSES)]
    for g in STATUSES:
        lines.append(f"| {g} | " + " | ".join(str(metrics["confusion"].get((g, p), 0)) for p in STATUSES) + " |")
    if diff and metrics["failures"]:
        lines += ["", "| case | gold | pred | missing steps | extra steps |", "|---|---|---|---|---|"]
        for f in metrics["failures"]:
            fmt = lambda items: "<br>".join(" ".join(x for x in t if x) for t in items) or "-"  # noqa: E731
            lines.append(f"| {f['case_id']} | {f['gold_status']} | {f['pred_status']} | {fmt(f['missing'])} | {fmt(f['extra'])} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=DATASET_DIR / "sample_output.csv")
    parser.add_argument("--diff", action="store_true", help="print per-case differences")
    args = parser.parse_args()
    gold = read_output(args.gold)
    pred = {k: v for k, v in read_output(args.pred).items() if k in gold}
    print(format_report(score(pred, gold), f"{args.pred.name} vs {args.gold.name}", diff=args.diff))


if __name__ == "__main__":
    main()
