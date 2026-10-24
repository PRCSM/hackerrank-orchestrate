"""Turn resource states + policy into ordered FINDING/REMEDIATION/VERIFICATION steps.

For every linked resource the planner compares the state at audit_at with the
treatment `deletion_rules.csv` requires:

  requirement   violating state             finding / remediation / verification
  DELETE        record present              REMAINING_RECORD / DELETE / VERIFY_ABSENT
  REDACT        direct identifier exposed   EXPOSED_PII / REDACT / VERIFY_REDACTED
  ANONYMIZE     still attributable          EXPOSED_PII / ANONYMIZE / VERIFY_ANONYMIZED
  BLOCK         restore allowed             RESTORE_RISK / BLOCK_RESTORE / VERIFY_RESTORE_BLOCKED
  DISABLE       connector enabled           ACTIVE_DATA_FLOW / DISABLE / VERIFY_DISABLED
  RETAIN        (must stay)                 - / - / VERIFY_PRESENT

A compliant resource contributes only its verification. The account profile
is always verified absent. Any finding makes the case INCOMPLETE; findings
cite the observation that proves them, remediation and post-remediation
checks carry an empty evidence_id. A case without findings is COMPLETE and its
verifications cite the observation proving each compliant state.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.state import Fact, ResourceState
from policy.rules import Policy

PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")
OPERATION_ORDER = {
    "FINDING": ("REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"),
    "REMEDIATION": ("DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"),
    "VERIFICATION": (
        "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
        "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
    ),
}
OPERATION_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
# requirement -> (finding, remediation, verification) when violated
VIOLATION_STEPS = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "BLOCK": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "DISABLE": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
}


@dataclass(frozen=True)
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    evidence_id: str = ""

    @property
    def state(self) -> str:
        return OPERATION_STATE[self.operation]

    def sort_key(self) -> tuple:
        return (PHASES.index(self.phase), OPERATION_ORDER[self.phase].index(self.operation), self.system_id, self.resource_id)


@dataclass
class Assessment:
    """Planner verdict for one resource (kept in the trace)."""

    resource_id: str
    system_id: str
    category: str
    requirement: str
    compliant: bool | None
    finding: Step | None = None
    remediation: Step | None = None
    verification: Step | None = None
    proof_evidence: str = ""
    note: str = ""


@dataclass
class CasePlan:
    case_id: str
    status: str
    steps: list[Step] = field(default_factory=list)
    assessments: list[Assessment] = field(default_factory=list)

    def numbered(self) -> list[dict]:
        return [
            {"step_no": i, "phase": s.phase, "system_id": s.system_id, "resource_id": s.resource_id,
             "operation": s.operation, "state": s.state, "evidence_id": s.evidence_id}
            for i, s in enumerate(self.steps, start=1)
        ]


def _evidence(fact: Fact | None) -> str:
    return fact.evidence.evidence_id if fact else ""


def assess(state: ResourceState, policy: Policy) -> Assessment:
    """Compare one resource's state at audit_at with its required treatment."""
    requirement = policy.required_action(state.category)
    a = Assessment(state.resource_id, state.system_id, state.category, requirement, compliant=None)
    sid, rid = state.system_id, state.resource_id

    def violated(fact: Fact) -> Assessment:
        finding, remediation, verification = VIOLATION_STEPS[requirement]
        a.compliant = False
        a.finding = Step("FINDING", sid, rid, finding, fact.evidence.evidence_id)
        a.remediation = Step("REMEDIATION", sid, rid, remediation)
        a.verification = Step("VERIFICATION", sid, rid, verification)
        return a

    def satisfied(operation: str, fact: Fact | None) -> Assessment:
        a.compliant = True
        a.proof_evidence = _evidence(fact)
        a.verification = Step("VERIFICATION", sid, rid, operation, a.proof_evidence)
        return a

    if requirement == "RETAIN":
        return satisfied("VERIFY_RECORD_PRESENT", state.presence if state.is_present else None)
    if requirement == "DISABLE":
        if state.flow_active is None:
            a.note = "no readback of the connector state"
            return a
        return violated(state.flow_active) if state.flow_active.value else satisfied("VERIFY_DATA_FLOW_DISABLED", state.flow_active)
    if requirement == "BLOCK":
        if state.restore_allowed is None:
            a.note = "no readback of the restore policy"
            return a
        return violated(state.restore_allowed) if state.restore_allowed.value else satisfied("VERIFY_RESTORE_BLOCKED", state.restore_allowed)
    if state.is_present is False:
        return satisfied("VERIFY_RECORD_ABSENT", state.presence)
    if requirement == "DELETE":
        if state.is_present:
            return violated(state.presence)
        a.note = "linked but never observed"
        return a
    exposure = state.attributable if requirement == "ANONYMIZE" else state.identifiable
    operation = "VERIFY_RECORD_ANONYMIZED" if requirement == "ANONYMIZE" else "VERIFY_RECORD_REDACTED"
    if exposure is None:
        if state.is_present:
            # Present and linked to the subject by a lookup key, with no field
            # readback proving the identifiers were removed: still exposed.
            return violated(state.presence)
        a.note = "linked but never observed"
        return a
    return violated(exposure) if exposure.value else satisfied(operation, exposure)


def build_plan(case_id: str, states: list[ResourceState], policy: Policy, account_ids: set[str]) -> CasePlan:
    assessments = [assess(s, policy) for s in states]
    for a in assessments:
        if a.resource_id in account_ids and a.verification is None:
            # The profile must always end absent, even when never observed.
            a.verification = Step("VERIFICATION", a.system_id, a.resource_id, "VERIFY_RECORD_ABSENT")
            a.note = a.note or "account verified absent by policy"
    incomplete = any(a.finding for a in assessments)
    steps: list[Step] = []
    for a in assessments:
        if incomplete:
            steps += [s for s in (a.finding, a.remediation) if s]
            if a.verification:
                steps.append(Step("VERIFICATION", a.system_id, a.resource_id, a.verification.operation))
        elif a.verification and a.verification.evidence_id:
            steps.append(a.verification)
        elif a.verification:
            # A COMPLETE answer may only claim what evidence proves.
            a.note = (a.note + "; " if a.note else "") + "verification dropped: no proving evidence at audit_at"
    steps = sorted(set(steps), key=Step.sort_key)
    return CasePlan(case_id, "INCOMPLETE" if incomplete else "COMPLETE", steps, assessments)


def not_applicable(case_id: str) -> CasePlan:
    return CasePlan(case_id, "NOT_APPLICABLE")
