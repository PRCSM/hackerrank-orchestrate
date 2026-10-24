"""Planner, validator and writer behaviour on hand-built states (T6, T8, T9, T11)."""
from __future__ import annotations

from config import DATASET_DIR
from graph.state import Fact, ResourceState
from ingest.observation import Observation
from ingest.requests import DeletionRequest
from policy.planner import build_plan
from policy.rules import Policy
from storage.writer import steps_to_json
from validate.validator import validate_case

AUDIT = "2026-09-15T12:00:00Z"
POLICY = Policy(DATASET_DIR)
REQUEST = DeletionRequest("case_x", "a@b.com", "2026-09-01T00:00:00Z", "COMPLETE", "2026-09-02T00:00:00Z", AUDIT, "delete")


def _obs(resource_id: str, evidence: str = "evidence_92", observed: str = "2026-09-15T10:00:00Z", **facts) -> Observation:
    from ingest.observation import system_for_resource
    return Observation(evidence_id=evidence, source_type="file", captured_at=observed, observed_at=observed,
                       system_id=system_for_resource(resource_id), resource_id=resource_id, **facts)


def _state(resource_id: str, category: str, **facts: Observation) -> ResourceState:
    from ingest.observation import system_for_resource
    def fact(name):
        o = facts.get(name)
        return Fact(bool(getattr(o, "attributable" if name == "attributable" else name)), o) if o else None
    return ResourceState(resource_id, system_for_resource(resource_id), category, fact("presence"), fact("identifiable"),
                         fact("flow_active"), fact("restore_allowed"), fact("attributable"), list(facts.values()))


def test_claimed_failure_but_readback_absent_is_complete():
    # T6: the activity log says FAILED, the readback says 404 -> verified absent.
    absent = _obs("account_3136", presence=False)
    retained = _obs("audit_record_451", presence=True)
    plan = build_plan("c", [_state("account_3136", "USER_PROFILE", presence=absent),
                            _state("audit_record_451", "SECURITY_AUDIT_LOG", presence=retained)], POLICY, {"account_3136"})
    assert plan.status == "COMPLETE"
    assert [(s.operation, s.evidence_id) for s in plan.steps] == [
        ("VERIFY_RECORD_ABSENT", "evidence_92"), ("VERIFY_RECORD_PRESENT", "evidence_92")]


def test_incomplete_plan_orders_and_blanks_future_evidence():
    # T8/T9: findings cite evidence; remediation and verification are future work.
    flow = _obs("data_flow_1", flow_active=True)
    search = _obs("search_1", presence=True)
    analytics = _obs("analytics_1", presence=True, identifiable=True)
    backup = _obs("backup_1", restore_allowed=True)
    states = [
        _state("search_1", "SEARCH_INDEX_ENTRY", presence=search),
        _state("analytics_1", "ANALYTICS_PROFILE", presence=analytics, identifiable=analytics, attributable=analytics),
        _state("data_flow_1", "DATA_FLOW", flow_active=flow),
        _state("backup_1", "BACKUP_ARCHIVE", restore_allowed=backup),
        _state("account_1", "USER_PROFILE"),
    ]
    plan = build_plan("c", states, POLICY, {"account_1"})
    assert plan.status == "INCOMPLETE"
    ops = [(s.phase, s.operation, s.resource_id, s.evidence_id) for s in plan.steps]
    assert ops == [
        ("FINDING", "REPORT_REMAINING_RECORD", "search_1", "evidence_92"),
        ("FINDING", "REPORT_EXPOSED_PII", "analytics_1", "evidence_92"),
        ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "data_flow_1", "evidence_92"),
        ("FINDING", "REPORT_RESTORE_RISK", "backup_1", "evidence_92"),
        ("REMEDIATION", "DISABLE_DATA_FLOW", "data_flow_1", ""),
        ("REMEDIATION", "BLOCK_RESTORE", "backup_1", ""),
        ("REMEDIATION", "DELETE_RECORD", "search_1", ""),
        ("REMEDIATION", "ANONYMIZE_RECORD", "analytics_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "analytics_1", ""),
        ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "data_flow_1", ""),
        ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "backup_1", ""),
    ]
    assert validate_case(REQUEST, plan.status, plan.numbered(), {"evidence_92": "2026-09-15T11:44:55Z"}) == []


def test_tombstone_keeps_reference_but_not_direct_identifiers():
    exposed = _obs("tombstone_9", presence=True, identifiable=True, references_user=True)
    plan = build_plan("c", [_state("tombstone_9", "ACCOUNT_TOMBSTONE", presence=exposed, identifiable=exposed)], POLICY, set())
    assert [s.operation for s in plan.steps] == ["REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"]
    clean = _obs("tombstone_9", presence=True, identifiable=False, references_user=True)
    plan = build_plan("c", [_state("tombstone_9", "ACCOUNT_TOMBSTONE", presence=clean, identifiable=clean)], POLICY, set())
    assert [s.operation for s in plan.steps] == ["VERIFY_RECORD_REDACTED"]


def test_analytics_with_only_user_reference_is_not_anonymized():
    ref = _obs("analytics_7", presence=True, identifiable=False, references_user=True)
    plan = build_plan("c", [_state("analytics_7", "ANALYTICS_PROFILE", presence=ref, identifiable=ref, attributable=ref)], POLICY, set())
    assert plan.steps[0].operation == "REPORT_EXPOSED_PII"


def test_validator_rejects_future_evidence_and_bad_shapes():
    step = {"step_no": 1, "phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_1",
            "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_02"}
    assert any("after audit_at" in p for p in validate_case(REQUEST, "COMPLETE", [step], {"evidence_02": "2026-09-17T12:00:00Z"}))
    assert validate_case(REQUEST, "NOT_APPLICABLE", [step], {}) == ["NOT_APPLICABLE must have []"]
    bad = dict(step, state="PRESENT")
    assert any("invalid" in p for p in validate_case(REQUEST, "COMPLETE", [bad], {"evidence_02": AUDIT}))
    delete_retained = {"step_no": 1, "phase": "REMEDIATION", "system_id": "security_service", "resource_id": "audit_record_1",
                       "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""}
    problems = validate_case(REQUEST, "INCOMPLETE", [delete_retained], {}, {"audit_record_1": "SECURITY_AUDIT_LOG"})
    assert any("retained record" in p for p in problems)


def test_serialisation_is_deterministic():
    steps = [{"step_no": 1, "phase": "VERIFICATION", "system_id": "s", "resource_id": "r", "operation": "VERIFY_RECORD_ABSENT",
              "state": "ABSENT", "evidence_id": "e"}]
    assert steps_to_json(steps) == steps_to_json(list(reversed(steps)))
    assert steps_to_json(steps).startswith('[{"evidence_id":"e","operation"')
