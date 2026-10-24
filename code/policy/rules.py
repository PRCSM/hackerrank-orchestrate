"""deletion_rules.csv -> required treatment per data category.

The CSV covers record categories. Data flows are not a record category; the
operation table defines their treatment (disable), so it is added here. A
BACKUP_ARCHIVE is retained but its restore path must be blocked; retained
categories (security audit log, legal hold) must stay present.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

ACTIONS = ("DELETE", "REDACT", "ANONYMIZE", "RETAIN", "BLOCK", "DISABLE")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


class Policy:
    def __init__(self, dataset_dir: Path) -> None:
        with open(dataset_dir / "deletion_rules.csv", newline="", encoding="utf-8") as handle:
            self.rules = {row["data_category"]: Rule(**row) for row in csv.DictReader(handle)}

    def required_action(self, category: str) -> str:
        """DELETE | REDACT | ANONYMIZE | RETAIN | BLOCK | DISABLE."""
        if category == "DATA_FLOW":
            return "DISABLE"
        if category == "BACKUP_ARCHIVE":
            return "BLOCK"
        rule = self.rules.get(category)
        if rule is None:
            # Unknown categories fall back to the strictest personal-data rule.
            return "DELETE"
        return rule.required_action

    def rule_for(self, category: str) -> Rule | None:
        return self.rules.get(category)
