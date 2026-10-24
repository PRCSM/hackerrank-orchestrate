"""Deletion requests (evaluation and sample files)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeletionRequest:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


def load_requests(path: Path) -> list[DeletionRequest]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [DeletionRequest(**row) for row in csv.DictReader(handle)]


def case_sort_key(case_id: str) -> tuple[int, str]:
    """Numeric-aware ordering so case_100 sorts after case_99."""
    prefix, _, number = case_id.rpartition("_")
    return (int(number), prefix) if number.isdigit() else (10**9, case_id)
