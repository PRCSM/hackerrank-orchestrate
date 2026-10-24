"""Regression guard: the deterministic pipeline on the 32 labelled samples."""
from __future__ import annotations

from audit import Auditor
from config import DATASET_DIR
from evaluation.main import score
from ingest.requests import load_requests
from orchestrator import Orchestrator
from storage.writer import read_output


def test_sample_scores_do_not_regress(tmp_path):
    samples = load_requests(DATASET_DIR / "sample_deletion_requests.csv")
    everything = samples + load_requests(DATASET_DIR / "deletion_requests.csv")
    orchestrator = Orchestrator(Auditor(DATASET_DIR), use_llm=False, resume=False, trace_dir=tmp_path)
    results = orchestrator.run(samples, all_requests=everything)
    pred = {r.request.case_id: (r.plan.status, r.plan.numbered()) for r in results}
    metrics = score(pred, read_output(DATASET_DIR / "sample_output.csv"))
    assert metrics["status_accuracy"] == 1.0
    assert metrics["step_f1"] >= 0.96
    # T6: case_25's activity log says the account delete FAILED; readback is 404.
    assert pred["case_25"][0] == "COMPLETE"
