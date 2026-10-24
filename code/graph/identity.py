"""Identity resolution over current identities plus historical changes.

The request email may be current or historical. We resolve it by exact,
case-insensitive equality (never fuzzy or substring matching, because the
dataset contains lookalike decoys such as `name.jr@...`) and then collect every
identifier the user has had up to the audit time: emails, phones, the email
hash of every email, stored hashes, and the stable user_id.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ingest.observation import normalize_email, normalize_phone

IDENTITY_FIELDS = {"email", "phone", "email_hash"}


def email_hash(email: str) -> str:
    """sha256 of the lower-cased email, hex encoded (verified on the dataset)."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def _normalize(field_name: str, value: str) -> str:
    if field_name == "email":
        return normalize_email(value)
    if field_name == "phone":
        return normalize_phone(value)
    return value.strip().lower()


@dataclass(frozen=True)
class AuditChange:
    audit_id: str
    user_id: str
    event_time: str
    field_name: str
    old_value: str
    new_value: str


@dataclass
class Identity:
    """Everything that identifies one user as of a point in time."""

    user_id: str
    name: str
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)
    audit_ids: list[str] = field(default_factory=list)

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(self.emails | self.phones | self.hashes | {self.user_id})


class IdentityResolver:
    def __init__(self, dataset_dir: Path) -> None:
        with open(dataset_dir / "user_identities.csv", newline="", encoding="utf-8") as handle:
            self.current = {row["user_id"]: row for row in csv.DictReader(handle)}
        with open(dataset_dir / "user_audit_log.csv", newline="", encoding="utf-8") as handle:
            self.changes = [AuditChange(**row) for row in csv.DictReader(handle)]
        self._owners: dict[str, set[str]] = {}
        for user_id in self.current:
            for key in self.identity(user_id).keys:
                self._owners.setdefault(key, set()).add(user_id)

    def identity(self, user_id: str, at: str | None = None) -> Identity:
        """All identifiers of `user_id`, ignoring audit rows after `at`."""
        row = self.current[user_id]
        ident = Identity(user_id=user_id.lower(), name=row["name"])
        ident.emails.add(normalize_email(row["email"]))
        ident.phones.add(normalize_phone(row["phone"]))
        ident.hashes.add(row["email_hash"].strip().lower())
        for change in self.changes:
            if change.user_id != user_id or change.field_name not in IDENTITY_FIELDS:
                continue
            if at is not None and change.event_time > at:
                continue
            ident.audit_ids.append(change.audit_id)
            for value in (change.old_value, change.new_value):
                if value:
                    target = {"email": ident.emails, "phone": ident.phones, "email_hash": ident.hashes}[change.field_name]
                    target.add(_normalize(change.field_name, value))
        ident.hashes |= {email_hash(e) for e in ident.emails}
        return ident

    def resolve(self, email: str, at: str | None = None) -> list[Identity]:
        """Users whose current or historical email equals `email` (exactly)."""
        wanted = normalize_email(email)
        matches = []
        for user_id in sorted(self._owners.get(wanted, set())):
            ident = self.identity(user_id, at)
            if wanted in ident.emails:
                matches.append(ident)
        return matches

    def owners(self, key: str) -> set[str]:
        """Which users a normalised key belongs to (empty for unknown keys)."""
        return self._owners.get(key, set())

    @property
    def known_keys(self) -> frozenset[str]:
        return frozenset(self._owners)
