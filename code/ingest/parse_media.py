"""Screenshots (PNG) and PDFs: OCR / text extraction, then grounded regexes.

Business screens show one subject at a time (a customer page, a connector
list, a support ticket), so the parser extracts the resource ids on the page
(tolerating OCR separators such as `data_flow.1121`) and the identifiers it
displays. State comes from what the page shows: a rendered record exists, a
visible email/phone means the record still exposes the person, a connector in
"Continuous"/"Active" mode is running, a recovery console that lists restores
can restore. Resources can also be recognised through aliases that activity
rows declare (e.g. `object_19 (delivery_confirmation_19.pdf)`).

Extraction is cached in .cache/media/ keyed by file content hash, so the
(slow) OCR step runs once per file.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from config import CACHE_DIR
from ingest.manifest import EvidenceFile
from ingest.observation import EMAIL_PATTERN, RESOURCE_ID_PATTERN, Observation, candidate_keys, system_for_resource
from ingest.parsed import ParsedFile

USER_REF = re.compile(r"\buser[_ .](\d{1,4})\b")
PHONE_TEXT = re.compile(r"\+\d{1,3}[\d\s-]{7,16}\d")
FLOW_ON_WORDS = ("continuous", "active", "running", "streaming")
FLOW_OFF_WORDS = ("paused", "disabled", "stopped")
RESTORE_WORDS = ("restored", "restore request", "recovery")


def extract_text(path: Path) -> str:
    """Return the visible text of a PNG (tesseract) or PDF (pdftotext), cached."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    cache = CACHE_DIR / "media" / f"{path.stem}-{digest}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    if path.suffix.lower() == ".pdf":
        command = ["pdftotext", "-layout", str(path), "-"]
    else:
        command = ["tesseract", str(path), "-", "--psm", "3"]
    text = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _alias_applies(alias: str, resource_id: str, evidence: EvidenceFile) -> bool:
    """A bare user_id alias (tombstones) is only trusted on a page produced by
    the resource's own system; any page may mention a user id."""
    if not alias.startswith("user_"):
        return True
    return system_for_resource(resource_id).split("_")[0] in evidence.origin


def _mentions(line: str, resource_id: str, aliases: dict[str, str]) -> bool:
    if any(f"{m.group(1)}_{m.group(2)}" == resource_id for m in RESOURCE_ID_PATTERN.finditer(line)):
        return True
    squashed = _squash(line)
    return any(rid == resource_id and _squash(alias) in squashed for alias, rid in aliases.items())


def _identity_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for email in EMAIL_PATTERN.findall(text):
        keys |= candidate_keys(email)
    for phone in PHONE_TEXT.findall(text):
        keys |= candidate_keys(phone)
    keys |= {f"user_{m.group(1)}" for m in USER_REF.finditer(text.lower())}
    return keys


def parse_media(evidence: EvidenceFile, aliases: dict[str, str]) -> ParsedFile:
    """`aliases` maps an alias (file name, statement number) to a resource id."""
    parsed = ParsedFile(file_id=evidence.file_id)
    text = extract_text(evidence.path)
    lowered = text.lower()
    resources = {f"{m.group(1)}_{m.group(2)}" for m in RESOURCE_ID_PATTERN.finditer(text)}
    squashed = _squash(text)
    resources |= {
        rid for alias, rid in aliases.items()
        if _squash(alias) and _squash(alias) in squashed and _alias_applies(alias, rid, evidence)
    }
    exposes_identifier = bool(EMAIL_PATTERN.search(text) or PHONE_TEXT.search(text))
    parsed.units_total = len(resources)
    for resource_id in sorted(resources):
        # A page about a single record links it to every identifier shown; on
        # a list page only identifiers on the record's own row count.
        scope = text if len(resources) == 1 else "\n".join(
            line for line in text.splitlines() if _mentions(line, resource_id, aliases)
        )
        keys = _identity_keys(scope)
        system = system_for_resource(resource_id)
        flow = None
        if system == "integration_service":
            flow = True if any(w in lowered for w in FLOW_ON_WORDS) else (False if any(w in lowered for w in FLOW_OFF_WORDS) else None)
        restore = True if system == "backup_service" and any(w in lowered for w in RESTORE_WORDS) else None
        parsed.observations.append(Observation(
            evidence_id=evidence.file_id,
            source_type="file",
            captured_at=evidence.captured_at,
            observed_at=evidence.captured_at,
            system_id=system,
            resource_id=resource_id,
            category="SUPPORT_ATTACHMENT" if "attachment" in lowered and system in {"support_platform", "object_storage"} else None,
            keys=frozenset(keys),
            presence=True,
            identifiable=exposes_identifier if system not in {"security_service", "backup_service", "integration_service"} else None,
            flow_active=flow,
            restore_allowed=restore,
            exposed_fields=("rendered_text",) if exposes_identifier else (),
            raw_ref=f"{evidence.path.name}:ocr",
            summary=" ".join(text.split())[:400],
        ))
    return parsed
