"""Checkpoint / resume for LLM work, keyed by (kind, case or batch, prompt hash).

A rerun with the same prompts and inputs spends zero tokens: the stored
result is returned and a cache hit is logged instead of a model call.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ResultCache:
    def __init__(self, directory: Path, enabled: bool = True) -> None:
        self.directory = directory
        self.enabled = enabled

    @staticmethod
    def key(kind: str, subject: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
        return f"{kind}-{subject}-{digest}"

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not self.enabled or not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, value: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps(value, sort_keys=True, indent=1), encoding="utf-8")
