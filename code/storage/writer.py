"""Deterministic serialisation of output.csv."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from ingest.requests import case_sort_key

HEADER = ("case_id", "deletion_status", "steps_json")


def steps_to_json(steps: list[dict]) -> str:
    """Compact JSON with sorted keys, so reruns are byte-identical."""
    return json.dumps(steps, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def write_output(rows: list[tuple[str, str, list[dict]]], path: Path) -> None:
    """rows: (case_id, status, numbered steps); written sorted by case id."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for case_id, status, steps in sorted(rows, key=lambda r: case_sort_key(r[0])):
            writer.writerow((case_id, status, steps_to_json(steps)))


def read_output(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return {row["case_id"]: (row["deletion_status"], json.loads(row["steps_json"])) for row in csv.DictReader(handle)}
