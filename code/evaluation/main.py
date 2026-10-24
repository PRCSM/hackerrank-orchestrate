"""Score predictions against labelled answers (sample_output.csv or custom cases).

Metrics
- status accuracy and a status confusion matrix;
- step-level precision / recall / F1 over the multiset of
  (phase, system_id, resource_id, operation, state, evidence_id) tuples;
- the same without evidence_id (did we find the right work at all?);
- field-level accuracy: per field, share of gold steps whose predicted step at
  the same position has the same value;
- exact sequence match: the whole steps array is identical.

Usage:
  python3 code/evaluation/main.py --pred PRED.csv [--gold dataset/sample_output.csv] [--diff]
  python3 code/evaluation/main.py --custom [--diff]   # run + score the hand-built cases
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


def _prf(hit: int, predicted: int, expected: int) -> tuple[float, float, float]:
    precision = hit / predicted if predicted else (1.0 if not expected else 0.0)
    recall = hit / expected if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def score(pred: dict[str, tuple[str, list[dict]]], gold: dict[str, tuple[str, list[dict]]]) -> dict:
    cases = sorted(gold, key=case_sort_key)
    confusion = Counter()
    # Steps are matched within a case only: a resource listed under the wrong
    # case is a miss there and an extra here, never a hit.
    counts = Counter()
    field_hits = Counter()
    gold_steps = 0
    exact = 0
    per_case = []
    for case_id in cases:
        g_status, g_steps = gold[case_id]
        p_status, p_steps = pred.get(case_id, ("MISSING", []))
        confusion[(g_status, p_status)] += 1
        pt, gt = _tuples(p_steps), _tuples(g_steps)
        pn, gn = _tuples(p_steps, False), _tuples(g_steps, False)
        counts["hit"] += sum((pt & gt).values())
        counts["hit_noev"] += sum((pn & gn).values())
        counts["pred"] += len(p_steps)
        counts["gold"] += len(g_steps)
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
    p, r, f = _prf(counts["hit"], counts["pred"], counts["gold"])
    p2, r2, f2 = _prf(counts["hit_noev"], counts["pred"], counts["gold"])
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


CUSTOM_DIR = Path(__file__).resolve().parent / "custom_cases"


def run_custom(diff: bool) -> str:
    """Run the deterministic pipeline on the hand-built dataset and score it."""
    import tempfile

    from audit import Auditor
    from ingest.requests import load_requests
    from orchestrator import Orchestrator

    dataset = CUSTOM_DIR / "data"
    requests = load_requests(dataset / "deletion_requests.csv")
    with tempfile.TemporaryDirectory() as scratch:
        orchestrator = Orchestrator(Auditor(dataset), client=None, resume=False, trace_dir=Path(scratch), cache_dir=Path(scratch))
        results = orchestrator.run(requests, all_requests=requests)
    pred = {r.request.case_id: (r.plan.status, r.plan.numbered()) for r in results}
    return format_report(score(pred, read_output(CUSTOM_DIR / "expected_output.csv")), "custom cases", diff=diff)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pred", type=Path)
    parser.add_argument("--gold", type=Path, default=DATASET_DIR / "sample_output.csv")
    parser.add_argument("--custom", action="store_true", help="run and score the custom cases")
    parser.add_argument("--diff", action="store_true", help="print per-case differences")
    args = parser.parse_args()
    if args.custom:
        print(run_custom(args.diff))
        return
    if args.pred is None:
        parser.error("--pred is required unless --custom is given")
    gold = read_output(args.gold)
    pred = {k: v for k, v in read_output(args.pred).items() if k in gold}
    print(format_report(score(pred, gold), f"{args.pred.name} vs {args.gold.name}", diff=args.diff))


if __name__ == "__main__":
    main()
