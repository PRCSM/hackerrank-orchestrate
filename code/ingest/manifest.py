"""Evidence manifest: file_id -> path and capture time (the citation cutoff)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    path: Path
    origin: str
    captured_at: str

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()


def load_manifest(dataset_dir: Path) -> list[EvidenceFile]:
    with open(dataset_dir / "evidence_files.csv", newline="", encoding="utf-8") as handle:
        return [
            EvidenceFile(
                file_id=row["file_id"],
                path=dataset_dir / row["file_path"],
                origin=row["origin"],
                captured_at=row["captured_at"],
            )
            for row in csv.DictReader(handle)
        ]
