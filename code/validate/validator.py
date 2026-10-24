"""The last gate before output.csv: schema, vocabulary, ordering, evidence, policy.

`validate_case` returns a list of problems for one row (empty = valid);
`validate_output` checks the whole file. Nothing that fails here is written.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from ingest.requests import DeletionRequest, case_sort_key
from policy.planner import OPERATION_ORDER, OPERATION_STATE, PHASES

STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
RETAINED_CATEGORIES = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD", "BACKUP_ARCHIVE"}


def _sort_key(step: dict) -> tuple:
    phase = step["phase"]
    return (PHASES.index(phase), OPERATION_ORDER[phase].index(step["operation"]), step["system_id"], step["resource_id"])


def validate_case(
    request: DeletionRequest,
    status: str,
    steps: list[dict],
    evidence_time: dict[str, str],
    categories: dict[str, str] | None = None,
) -> list[str]:
    """evidence_time maps every citable id (file/activity/audit) to its time."""
    problems: list[str] = []
    if status not in STATUSES:
        return [f"unknown status {status!r}"]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have []"]
    if not steps:
        problems.append(f"{status} case without steps")
    for index, step in enumerate(steps, start=1):
        if set(step) != STEP_KEYS:
            problems.append(f"step {index}: keys {sorted(step)}")
            continue
        if step["step_no"] != index:
            problems.append(f"step {index}: step_no {step['step_no']} not consecutive")
        phase, operation = step["phase"], step["operation"]
        if phase not in PHASES or operation not in OPERATION_ORDER[phase]:
            problems.append(f"step {index}: {operation} not allowed in {phase}")
            continue
        if step["state"] != OPERATION_STATE[operation]:
            problems.append(f"step {index}: state {step['state']} invalid for {operation}")
        evidence = step["evidence_id"]
        needs_evidence = phase == "FINDING" or status == "COMPLETE"
        if needs_evidence and not evidence:
            problems.append(f"step {index}: {phase} in {status} case must cite evidence")
        if not needs_evidence and evidence:
            problems.append(f"step {index}: future work must use empty evidence_id")
        if evidence:
            if evidence not in evidence_time:
                problems.append(f"step {index}: unknown evidence {evidence}")
            elif evidence_time[evidence] > request.audit_at:
                problems.append(f"step {index}: evidence {evidence} captured after audit_at")
        if categories and operation == "DELETE_RECORD" and categories.get(step["resource_id"]) in RETAINED_CATEGORIES:
            problems.append(f"step {index}: retained record {step['resource_id']} scheduled for deletion")
    if status == "COMPLETE" and any(s.get("phase") != "VERIFICATION" for s in steps):
        problems.append("COMPLETE case may contain only VERIFICATION steps")
    if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps):
        problems.append("INCOMPLETE case without findings")
    try:
        if [_sort_key(s) for s in steps] != sorted(_sort_key(s) for s in steps):
            problems.append("steps are not in phase/operation/system/resource order")
    except (KeyError, ValueError):
        problems.append("steps cannot be ordered")
    return problems


def validate_output(path: Path, requests: list[DeletionRequest], evidence_time: dict[str, str]) -> list[str]:
    problems: list[str] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        problems.append(f"bad header {header}")
    expected = [r.case_id for r in requests]
    got = [row[0] for row in rows]
    if len(rows) != len(requests):
        problems.append(f"{len(rows)} rows, expected {len(requests)}")
    if sorted(got, key=case_sort_key) != got:
        problems.append("rows not sorted by case_id")
    if set(got) != set(expected) or len(set(got)) != len(got):
        problems.append("case ids do not match deletion_requests.csv one-to-one")
    by_case = {r.case_id: r for r in requests}
    for case_id, status, steps_json in rows:
        try:
            steps = json.loads(steps_json)
        except json.JSONDecodeError:
            problems.append(f"{case_id}: invalid JSON")
            continue
        if case_id in by_case:
            problems += [f"{case_id}: {p}" for p in validate_case(by_case[case_id], status, steps, evidence_time)]
    return problems
