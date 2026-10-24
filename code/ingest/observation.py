"""Observation model shared by every parser.

An Observation is one grounded statement about one resource taken from one
evidence item (file, activity row or audit row): "at observed_at, resource R in
system S looked like this". Parsers only translate formats into flat field
dictionaries; `observation_from_fields` turns those fields into normalised
state facts with one set of semantics, so a 404 means the same thing whether
it came from JSON, SQL, a log line or an OCR'd screenshot.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SourceType = Literal["file", "activity", "audit"]

RESOURCE_PREFIX_SYSTEM: dict[str, str] = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "marketing": "marketing_platform",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "object": "object_storage",
    "legal_hold": "legal_service",
}

RESOURCE_ID_PATTERN = re.compile(
    r"\b(audit_record|data_flow|email_delivery|legal_hold|account|search|analytics|support|export|backup|"
    r"tombstone|marketing|cache|object)[ _.](\d{1,5})\b"
)
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
USER_ID_PATTERN = re.compile(r"^user_\d+$")
PHONE_PATTERN = re.compile(r"^\+?[\d\s().-]{7,}$")

# Stored fields that hold a direct identifier. A non-null value means the
# record still exposes the person; query keys (lookup_key, correlation_key...)
# only say how the record was found and are therefore not listed here.
DIRECT_IDENTIFIER_FIELDS = frozenset({
    "email", "phone", "email_hash", "full_name", "display_name", "name",
    "requester_name", "requester_email", "requester_phone", "owner_email",
    "owner_phone", "customer_email", "uploaded_by", "matched_value",
})
# A stable internal reference: allowed on tombstones, support records, backups
# and connectors, but it still makes analytics attributable to the user.
STABLE_REFERENCE_FIELDS = frozenset({"user_id", "customer_reference", "subject_reference", "subject_ref"})
REDACTED_MARKERS = frozenset({"[redacted]", "redacted", "***"})

PRESENT_RESULTS = frozenset({"hit", "found", "identifiable", "present", "exists", "ready", "active"})
ABSENT_RESULTS = frozenset({"miss", "not_found", "absent", "deleted", "none"})
DEIDENTIFIED_RESULTS = frozenset({"anonymized", "redacted"})

CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("contact_query", "SECONDARY_IDENTIFIER"),
    ("contact_lookup", "SECONDARY_IDENTIFIER"),
    ("email_opt_in", "MARKETING_PROFILE"),
    ("attachment", "SUPPORT_ATTACHMENT"),
    ("support_conversations", "SUPPORT_MESSAGE"),
    ("message_", "SUPPORT_MESSAGE"),
)


class Observation(BaseModel):
    """A single grounded fact about a resource at a point in time."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    source_type: SourceType
    captured_at: str
    observed_at: str
    system_id: str
    resource_id: str
    category: str | None = None
    keys: frozenset[str] = frozenset()
    presence: bool | None = None
    identifiable: bool | None = None
    flow_active: bool | None = None
    restore_allowed: bool | None = None
    references_user: bool | None = None
    exposed_fields: tuple[str, ...] = ()
    implicit: bool = False
    raw_ref: str = ""
    summary: str = ""

    @property
    def attributable(self) -> bool | None:
        """Can the record still be tied to a person (direct identifier OR a
        stable user reference)? Anonymisation must remove both."""
        if self.identifiable or self.references_user:
            return True
        if self.identifiable is False or self.references_user is False:
            return False
        return None


# ---------------------------------------------------------------- normalising
def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    return "tel:" + re.sub(r"\D", "", value)


def candidate_keys(value: Any) -> set[str]:
    """Normalise one raw value into the identity keys it could represent."""
    if value is None or isinstance(value, (bool, int, float)):
        return set()
    text = str(value).strip()
    if not text:
        return set()
    lowered = text.lower()
    keys: set[str] = set()
    if USER_ID_PATTERN.match(lowered):
        keys.add(lowered)
    elif HASH_PATTERN.match(lowered):
        keys.add(lowered)
    elif "@" in text:
        keys.update(normalize_email(m) for m in EMAIL_PATTERN.findall(text))
    elif PHONE_PATTERN.match(text) and sum(c.isdigit() for c in text) >= 8:
        keys.add(normalize_phone(text))
    return keys


def system_for_resource(resource_id: str) -> str:
    match = RESOURCE_ID_PATTERN.match(resource_id)
    prefix = match.group(1) if match else resource_id.rsplit("_", 1)[0]
    return RESOURCE_PREFIX_SYSTEM.get(prefix, "unknown_system")


def canonical_resource_id(text: str) -> str | None:
    """Return `prefix_number` for OCR-ish variants such as `data_flow.1121`."""
    match = RESOURCE_ID_PATTERN.search(text)
    return f"{match.group(1)}_{match.group(2)}" if match else None


# ---------------------------------------------------------------- semantics
def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "null", "none"})


def _is_redacted(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in REDACTED_MARKERS


def _presence(fields: dict[str, Any]) -> bool | None:
    status = fields.get("http_status", fields.get("status_code"))
    if status is not None and str(status).isdigit():
        return int(str(status)) < 400
    result = str(fields.get("result", "")).strip().lower()
    if result in PRESENT_RESULTS or result in DEIDENTIFIED_RESULTS or result in {"blocked", "disabled"}:
        return True
    if result in ABSENT_RESULTS:
        return False
    for name in ("available", "download_available", "archive_present"):
        flag = _as_bool(fields.get(name))
        if flag is not None:
            return flag
    for name in ("rows", "total_hits", "total"):
        value = fields.get(name)
        if value is not None and str(value).isdigit():
            return int(str(value)) > 0
    if fields.get("__row__"):
        return True
    return None


def _identifiable(fields: dict[str, Any]) -> tuple[bool | None, tuple[str, ...]]:
    result = str(fields.get("result", "")).strip().lower()
    if result == "identifiable":
        return True, ("result",)
    if result in DEIDENTIFIED_RESULTS:
        return False, ()
    if str(fields.get("detector", "")).lower() == "direct_identifier":
        return True, ("matched_value",)
    exposed = tuple(sorted(
        name for name in DIRECT_IDENTIFIER_FIELDS
        if name in fields and not _is_null(fields[name]) and not _is_redacted(fields[name])
    ))
    message = fields.get("message_text")
    if isinstance(message, str) and EMAIL_PATTERN.search(message):
        exposed = tuple(sorted(set(exposed) | {"message_text"}))
    if exposed:
        return True, exposed
    if any(name in fields for name in DIRECT_IDENTIFIER_FIELDS) or _is_redacted(message):
        return False, ()
    return None, ()


def _flow_active(fields: dict[str, Any]) -> bool | None:
    result = str(fields.get("result", "")).strip().lower()
    if result in {"active", "disabled"} and fields.get("record_type") == "DATA_FLOW":
        return result == "active"
    enabled = _as_bool(fields.get("enabled"))
    if enabled is not None:
        return enabled
    status = str(fields.get("last_delivery_status", "")).lower()
    if status in {"delivered", "running"}:
        return True
    if status in {"stopped", "paused"}:
        return False
    return None


def _restore_allowed(fields: dict[str, Any]) -> bool | None:
    result = str(fields.get("result", "")).strip().lower()
    if fields.get("record_type") == "BACKUP_ARCHIVE" and result in {"active", "blocked"}:
        return result == "active"
    allowed = _as_bool(fields.get("restore_allowed"))
    if allowed is not None:
        return allowed
    decision = str(fields.get("decision", "")).lower()
    if decision in {"deny", "denied"}:
        return False
    if decision in {"allow", "allowed"}:
        return True
    eligible = _as_bool(fields.get("eligible"))
    blocked = _as_bool(fields.get("deletion_block"))
    if eligible is not None or blocked is not None:
        return bool(eligible) and not bool(blocked)
    return None


def _references_user(fields: dict[str, Any]) -> bool | None:
    named = [name for name in STABLE_REFERENCE_FIELDS if name in fields]
    if not named:
        return None
    return any(not _is_null(fields[name]) and not _is_redacted(fields[name]) for name in named)


def category_hint(fields: dict[str, Any]) -> str | None:
    explicit = fields.get("record_type")
    if explicit:
        return str(explicit)
    blob = " ".join(f"{k} {v}" for k, v in fields.items() if k in {
        "event", "collection", "collection_name", "storage_class", "email_opt_in", "__table__", "file", "file_name",
    }).lower()
    if "email_opt_in" in fields:
        blob += " email_opt_in"
    for needle, category in CATEGORY_HINTS:
        if needle in blob:
            return category
    return None


def observation_from_fields(
    fields: dict[str, Any],
    *,
    evidence_id: str,
    source_type: SourceType,
    captured_at: str,
    observed_at: str,
    raw_ref: str,
) -> Observation | None:
    """Translate a flat field dictionary into an Observation (None if no resource)."""
    resource_id = canonical_resource_id(str(fields.get("resource_id", "")))
    if not resource_id:
        return None
    # The resource-id prefix is authoritative; a stated system is only a fallback.
    system_id = system_for_resource(resource_id)
    if system_id == "unknown_system":
        system_id = str(fields.get("system") or fields.get("origin_system") or system_id)
    keys: set[str] = set()
    for name, value in fields.items():
        if name.startswith("__") or name in {"resource_id", "source_resource_id"}:
            continue
        keys |= candidate_keys(value)
    identifiable, exposed = _identifiable(fields)
    references_user = _references_user(fields)
    presence = _presence(fields)
    if presence is None and (identifiable is not None or references_user is not None):
        presence = True  # stored fields were read back, so the record exists
    summary = ", ".join(
        f"{k}={v}" for k, v in sorted(fields.items()) if not k.startswith("__") and not isinstance(v, (dict, list))
    )
    return Observation(
        evidence_id=evidence_id,
        source_type=source_type,
        captured_at=captured_at,
        observed_at=observed_at or captured_at,
        system_id=system_id,
        resource_id=resource_id,
        category=category_hint(fields),
        keys=frozenset(keys),
        presence=presence,
        identifiable=identifiable,
        flow_active=_flow_active(fields),
        restore_allowed=_restore_allowed(fields),
        references_user=references_user,
        exposed_fields=exposed,
        raw_ref=raw_ref,
        summary=summary[:400],
    )
