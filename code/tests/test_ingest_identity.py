"""Data facts T1-T5: time cutoff, historical identity, hashes, decoys, case noise."""
from __future__ import annotations

import hashlib

from graph.evidence_graph import usable
from graph.identity import email_hash
from ingest.observation import candidate_keys


def test_t1_evidence_after_audit_is_invisible(graph):
    # evidence_02 (snapshot bundle exported 09-17) shows account_5684 ACTIVE,
    # but a case audited on 09-15 must not see it.
    later = [o for o in graph.by_resource["account_5684"] if o.evidence_id == "evidence_02"]
    assert later and all(not usable(o, "2026-09-15T12:00:00Z") for o in later)
    visible = graph.resource_observations("account_5684", "2026-09-15T12:00:00Z")
    assert {o.evidence_id for o in visible} == {"evidence_92"}
    assert all(o.presence is False for o in visible)


def test_t1_activities_after_audit_are_invisible(graph):
    obs = graph.resource_observations("data_flow_8165", "2026-01-01T00:00:00Z")
    assert obs == []


def test_t2_historical_email_resolves_and_collects_all_keys(resolver):
    [ident] = resolver.resolve("minjun.kim90@gmail.com", "2026-09-15T12:00:00Z")
    assert ident.user_id == "user_12"
    assert {"minjun.kim@gmail.com", "minjun.kim90@gmail.com"} <= ident.emails
    assert email_hash("minjun.kim90@gmail.com") in ident.hashes
    assert "user_12" in ident.keys


def test_t2_audit_rows_after_audit_time_are_ignored(resolver):
    # user_12 changed email on 2026-05-18; before that the old address only.
    early = resolver.identity("user_12", at="2026-05-01T00:00:00Z")
    assert "audit_282" not in early.audit_ids


def test_t3_email_hash_is_sha256_of_lowercase_email(resolver):
    row = resolver.current["user_01"]
    assert email_hash(row["email"].upper()) == row["email_hash"]
    assert email_hash("A@B.COM") == hashlib.sha256(b"a@b.com").hexdigest()


def test_t3_hashed_correlation_key_links_record(graph, resolver):
    # analytics_9914 shows no email, only the hash of user_12's old email.
    [ident] = resolver.resolve("minjun.kim@gmail.com", "2026-09-15T12:00:00Z")
    hits = {o.resource_id for o in graph.observations_for_keys(ident.keys, "2026-09-15T12:00:00Z")}
    assert "analytics_9914" in hits


def test_t4_lookalike_decoys_are_not_attributed(graph, resolver):
    [silva] = resolver.resolve("g.silva76@gmail.com", "2026-09-15T12:00:00Z")
    hits = {o.resource_id for o in graph.observations_for_keys(silva.keys, "2026-09-15T12:00:00Z")}
    assert "search_428" in hits and "search_1874" not in hits  # search_1874 is g.silva76.jr
    assert resolver.resolve("nadia.popescu@yahoo.com") == [] or all(
        "nadia.ionescu@yahoo.com" not in i.emails for i in resolver.resolve("nadia.popescu@yahoo.com")
    )
    [martinez] = resolver.resolve("smartinez6461@gmail.com")
    assert "smartinez6461.jr@gmail.com" not in martinez.emails


def test_t5_uppercase_identifiers_are_normalised():
    assert candidate_keys("  SIPHO.MOKOENA@GMAIL.COM ") == {"sipho.mokoena@gmail.com"}
    assert candidate_keys("+82 10-5837-9451") == candidate_keys("+82 10 5837 9451")
