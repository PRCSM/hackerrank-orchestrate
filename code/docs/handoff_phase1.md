# Handoff — Phase 1: ingest, evidence graph, identity

## Files created
- `config.py` — paths, Groq model routing, limits, price table (all env-overridable).
- `ingest/observation.py` — `Observation` (pydantic, frozen) + the single semantics layer
  `observation_from_fields()` that turns any flat field dict into state facts:
  `presence`, `identifiable` (+`exposed_fields`), `flow_active`, `restore_allowed`, `category` hint, `keys`.
- `ingest/parsed.py` — `ParsedFile` (observations, `enumerated_systems`, unit counts) + `flatten_record`.
- `ingest/manifest.py` — `evidence_files.csv` → `EvidenceFile(file_id, path, origin, captured_at)`.
- `ingest/parse_sql.py` — executes each `.sql` in in-memory SQLite; every row of every
  primary/replica table is a present copy; tables tell which systems the snapshot enumerates.
- `ingest/parse_json.py` — generic walker: every object with `resource_id` is a record; nearest
  `observed_at/recorded_at/captured_at/exported_at` is its time.
- `ingest/parse_logs.py` — six line styles (kv, JSON, bracket, pipe, syslog, requestId/message),
  grouped by trace id; lookup keys from "started" lines flow into the resource record as `trace_key_N`.
- `ingest/parse_text.py` — handover `- time | system | resource | type | result | subject`.
- `ingest/parse_media.py` — tesseract (PNG) / pdftotext (PDF), cached in `.cache/media/`;
  OCR-tolerant resource ids (`data_flow.1121`), activity-declared aliases (file names, statement numbers).
- `ingest/parse_activity.py` — activities; only *observed* facts become state (CHECK_DATA_FLOW,
  CHECK_RESTORE_ACCESS, "repopulated downstream resource"); LINK_RESOURCE_IDENTITY → keys + aliases.
- `ingest/requests.py`, `ingest/loader.py` (graph build + coverage report).
- `graph/identity.py` — `IdentityResolver.resolve(email, at)`; exact case-insensitive matching;
  keys = all emails, phones, sha256(lower(email)) for every email, stored hashes, user_id.
- `graph/evidence_graph.py` — index by resource and key; `usable(obs, before)` = file captured_at ≤
  audit_at AND observed_at ≤ audit_at; `absence_by_enumeration()` (SQL snapshot omission ⇒ ABSENT).
- `graph/linkage.py` — key / recreation / batch linkage (see below).
- `tests/test_ingest_identity.py` — T1–T5 (8 tests, all pass).

## Decisions
- **`code/io/` renamed `code/storage/`**: `python3 code/main.py` puts `code/` on `sys.path`, so a
  package named `io` would shadow the stdlib `io` module and break Python itself.
- **State from readback only**: `reported_result` never sets state. Activity rows become observations
  only when their text states a checked fact (T6).
- **Exposure = stored direct identifiers** (email, phone, hash, names, requester/owner/customer email,
  uploaded_by, PII-scan matches). Query keys (`lookup_key`, `correlation_key`) link but do not expose.
  `customer_reference`/`user_id` are stable references (allowed on tombstones/support/backups).
- **Log trace merge**: only identity keys travel from resource-less lines (cache misses would otherwise
  flip a hit into a miss).
- **Media keys scoped**: on a list page only identifiers on the resource's own OCR line count; a
  bare `user_N` alias is trusted only on a page produced by that resource's system (the legal-hold PDF
  lists user ids and wrongly attached tombstone_17 to six users before this fix).
- **Identity cutoff**: audit-log rows after `audit_at` are ignored (T1/T2); current snapshot always used.
- **T7 linkage — batch inference**: 404 accounts and fully anonymised analytics carry no identifier at
  all in pre-audit evidence (e.g. analytics_1895 / account_3136 for case_25 appear only as nulls in
  evidence_92 and in `deletion_worker` rows). The worker processes each request in a batch just before
  `reported_at`: primary-profile submission 0–2 h before, "Policy action completed" 8–30 min before.
  Rule: an unowned resource in that window is attributed to the request; competing requests are
  separated by "does the case's own evidence file also observe it" and then by aligning processing order
  with case order. Ties are flagged `uncertain=True` (router input for Phase 3).

## Numbers
- Coverage: 94 files, 4061 units, 11 unparsed (legal_hold rows before the prefix was added → now 0
  unparsed SQL rows except 2 PDFs without resource ids), 3102 observations
  (file 2909, activity 193).
- Sample discoverability recall (every (system, resource) in sample_output found for the case):
  keys only **68.5%** → + batch inference (wide window) 85.6% (low precision) → tightened phrases/windows +
  ordered matching **86.5%**, extras down from 26 to 8.
- Unreachable from participant data before audit_at (documented, not fixable without leaking later
  evidence): audit_record_4902/6645/5569/3954/1707 (no pre-audit observation anywhere),
  audit_record_4128/7095 (only in evidence_02, captured after audit), account_004 (appears nowhere).
  8/111 ⇒ recall ceiling ≈ 92.8%.
- Remaining misses are genuine ties (two requests share one batch and one evidence file):
  case_25/26 analytics, case_27/28 accounts, case_22/23/32 accounts.

## Known gaps
- Ambiguous batch ties (~50% expected) — candidates for the investigator agent.
- OCR state heuristics for screenshots are keyword based.

## Next steps (Phase 2)
1. `graph/state.py`: latest usable observation per resource (+ enumeration absence), category inference.
2. `policy/rules.py` + `policy/planner.py`, `validate/validator.py`, `storage/writer.py`.
3. `evaluation/main.py` scorer; run `--no-llm --sample`; iterate general rules (T8–T11).
