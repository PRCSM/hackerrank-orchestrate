"""Deterministic multilingual intent gate (fallback for the LLM classifier).

A request authorizes deletion only when it asks for the account / personal
data to be deleted, erased, removed or closed. Requests that ask only for
access, export, a copy, a correction, an email/phone change, login help,
invoices, or a marketing opt-out are non-deletion — even if they contain the
word "remove" ("remove me from marketing lists only") or explicitly say not
to delete anything.
"""
from __future__ import annotations

import re

DELETION_PATTERNS = (
    r"\bdelet", r"\berase", r"\berasure\b", r"\bclose (my|the|this)? ?account", r"\bclosed my account\b",
    r"\bremove\b", r"\bpermanently\b", r"\b(is|was) gone\b", r"\bbeen (removed|deleted)\b",
    r"\bcannot identify me\b",
    r"eliminaci[oó]n", r"eliminar", r"exclus[aã]o", r"excluir", r"suppression", r"supprimer",
    r"削除", r"삭제", r"탈퇴",
)
NON_DELETION_PATTERNS = (
    r"\b(don'?t|do not|never) (delete|remove|erase|close)", r"\bcontinue using\b", r"\bstill use it\b",
    r"\bmarketing lists only\b", r"\bonly\b.*\bmarketing\b", r"\bstop (the )?promotional\b", r"\bunsubscribe\b",
    r"\b(send|give|need|want) (me )?a copy\b", r"\bportable format\b", r"\bdownload my\b", r"\bwhat personal information\b",
    r"\binvoice", r"\bmisspelled\b", r"\bout of date\b", r"\bchange the email\b", r"\breset(ting)? the login\b",
    r"\bcan'?t access my profile\b",
)
NO_MATCH = "no deletion instruction found"


def classify(text: str) -> tuple[bool, str]:
    """Return (authorizes_deletion, reason)."""
    lowered = text.lower()
    negative = next((p for p in NON_DELETION_PATTERNS if re.search(p, lowered)), None)
    positive = next((p for p in DELETION_PATTERNS if re.search(p, lowered)), None)
    if negative:
        return False, f"non-deletion request (matched {negative!r})"
    if positive:
        return True, f"deletion requested (matched {positive!r})"
    return False, NO_MATCH
