"""Ghost Data Still Exists — deletion-audit agent CLI.

    python3 code/main.py                 # all 518 cases -> ./output.csv
    python3 code/main.py --no-llm        # deterministic only (no API key needed)
    python3 code/main.py --sample        # the 32 labelled samples -> code/evaluation/sample_predictions.csv
    python3 code/main.py --cases case_33,case_34
    python3 code/main.py --no-resume     # ignore cached agent results

The deterministic pipeline (audit.py) answers every case. With GROQ_API_KEY
set and without --no-llm, the LLM intent classifier gates every request and
the investigator agent re-examines cases the baseline flags as uncertain; its
proposals are reconciled against the baseline before anything is written.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit import Auditor  # noqa: E402
from config import CACHE_DIR, CODE_DIR, DATASET_DIR, OUTPUT_PATH, TRACE_DIR, groq_api_key  # noqa: E402
from ingest.requests import load_requests  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from storage.usage import UsageRecorder  # noqa: E402
from storage.writer import write_output  # noqa: E402
from validate.validator import validate_case, validate_output  # noqa: E402

SAMPLE_OUTPUT = CODE_DIR / "evaluation" / "sample_predictions.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ghost Data Still Exists deletion-audit agent")
    parser.add_argument("--no-llm", action="store_true", help="deterministic mode only")
    parser.add_argument("--sample", action="store_true", help="run the labelled sample requests")
    parser.add_argument("--cases", default="", help="comma-separated case ids to run")
    parser.add_argument("--no-resume", action="store_true", help="ignore cached LLM results")
    parser.add_argument("--output", type=Path, default=None, help="output path override")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    evaluation = load_requests(DATASET_DIR / "deletion_requests.csv")
    samples = load_requests(DATASET_DIR / "sample_deletion_requests.csv")
    targets = samples if args.sample else evaluation
    if args.cases:
        wanted = set(args.cases.split(","))
        targets = [r for r in targets + (evaluation if args.sample else samples) if r.case_id in wanted]
    use_llm = not args.no_llm and groq_api_key() is not None
    if not args.no_llm and not use_llm:
        print("GROQ_API_KEY not set: running in deterministic mode (--no-llm).")
    auditor = Auditor(DATASET_DIR)
    client = None
    if use_llm:
        from agent.llm_client import GroqClient

        client = GroqClient(UsageRecorder(CACHE_DIR / "usage.jsonl"))
    orchestrator = Orchestrator(auditor, client=client, resume=not args.no_resume, trace_dir=TRACE_DIR)
    # Every request competes in deletion-batch attribution, whichever subset is printed.
    results = orchestrator.run(targets, all_requests=samples + evaluation)
    evidence_time = auditor.evidence_times()
    rows = []
    for result in results:
        steps = result.plan.numbered()
        categories = {a.resource_id: a.category for a in result.plan.assessments}
        problems = validate_case(result.request, result.plan.status, steps, evidence_time, categories)
        if problems:
            raise SystemExit(f"validator rejected {result.request.case_id}: {problems}")
        rows.append((result.request.case_id, result.plan.status, steps))
    output = args.output or (SAMPLE_OUTPUT if args.sample else OUTPUT_PATH)
    write_output(rows, output)
    if not args.sample and not args.cases:
        problems = validate_output(output, evaluation, evidence_time)
        if problems:
            raise SystemExit("output.csv failed validation:\n" + "\n".join(problems[:20]))
    statuses = {s: sum(1 for _, st, _ in rows if st == s) for s in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")}
    print(f"wrote {len(rows)} rows to {output} {statuses}")
    print(orchestrator.summary())


if __name__ == "__main__":
    main()
