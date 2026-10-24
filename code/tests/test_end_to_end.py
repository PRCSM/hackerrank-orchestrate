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
    orchestrator = Orchestrator(Auditor(DATASET_DIR), client=None, resume=False, trace_dir=tmp_path, cache_dir=tmp_path)
    results = orchestrator.run(samples, all_requests=everything)
    pred = {r.request.case_id: (r.plan.status, r.plan.numbered()) for r in results}
    metrics = score(pred, read_output(DATASET_DIR / "sample_output.csv"))
    assert metrics["status_accuracy"] == 1.0
    assert metrics["step_f1"] >= 0.96
    # T6: case_25's activity log says the account delete FAILED; readback is 404.
    assert pred["case_25"][0] == "COMPLETE"


def test_agent_mode_never_scores_below_baseline(tmp_path):
    """With an investigator that proposes junk or abstains, reconcile keeps the baseline."""
    import json

    from agent.llm_client import GroqClient
    from storage.usage import UsageRecorder
    from tests.test_agent_layer import FakeTransport

    samples = load_requests(DATASET_DIR / "sample_deletion_requests.csv")
    everything = samples + load_requests(DATASET_DIR / "deletion_requests.csv")
    intent_reply = {"content": json.dumps({"results": [
        {"case_id": r.case_id, "authorizes_deletion": True, "reason": "deletion"} for r in samples[:25]]})}
    intent_reply_2 = {"content": json.dumps({"results": [
        {"case_id": r.case_id, "authorizes_deletion": True, "reason": "deletion"} for r in samples[25:]]})}
    junk = {"content": json.dumps({"abstain": False, "summary": "x", "resources": [
        {"system_id": "account_service", "resource_id": "account_1", "linkage_reason": "made up"}]})}
    transport = FakeTransport([intent_reply, intent_reply_2] + [junk] * 40)
    client = GroqClient(UsageRecorder(tmp_path / "u.jsonl"), transport=transport, sleep=lambda s: None)
    auditor = Auditor(DATASET_DIR)
    agent_run = Orchestrator(auditor, client=client, resume=False, trace_dir=tmp_path, cache_dir=tmp_path).run(samples, everything)
    base_run = Orchestrator(auditor, client=None, resume=False, trace_dir=tmp_path, cache_dir=tmp_path).run(samples, everything)
    gold = read_output(DATASET_DIR / "sample_output.csv")
    agent = score({r.request.case_id: (r.plan.status, r.plan.numbered()) for r in agent_run}, gold)
    base = score({r.request.case_id: (r.plan.status, r.plan.numbered()) for r in base_run}, gold)
    assert agent["step_f1"] >= base["step_f1"] and agent["status_accuracy"] == base["status_accuracy"]
