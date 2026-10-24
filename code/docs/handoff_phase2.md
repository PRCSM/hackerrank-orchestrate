# Handoff — Phase 2: deterministic core (no LLM)

## Files created / changed
- `graph/state.py` — `resource_state_at()` → `ResourceState` with one `Fact(value, evidence)` per
  dimension: presence, identifiable, attributable (identifier OR stable user reference), flow_active,
  restore_allowed; `infer_category()` (specific hints > stated record_type > id prefix).
- `policy/rules.py` — `Policy.required_action(category)`; DATA_FLOW→DISABLE, BACKUP_ARCHIVE→BLOCK.
- `policy/planner.py` — `assess()` per resource, `build_plan()` per case, `Step`, `CasePlan`.
- `validate/validator.py` — `validate_case()` (keys, step_no, allowed (phase, op, state), evidence exists and
  ≤ audit_at, future work has "", COMPLETE only VERIFICATION, retained never deleted, ordering) and
  `validate_output()` (header, 518 rows, sorted, one-to-one ids, JSON).
- `storage/writer.py` (compact sorted-key JSON, csv), `storage/trace.py` (per-case trace).
- `audit.py` (`Auditor`: intent → linkage → states → plan → uncertainty flags), `orchestrator.py`
  (per-case try/except fallback, traces), `main.py` (CLI), `agent/intent_rules.py` (multilingual
  deterministic intent gate), `evaluation/main.py` (scorer).
- Tests: `tests/test_policy_planner.py`, `tests/test_end_to_end.py` (15 tests total, all pass).

## Iterations on the samples (`python3 code/main.py --no-llm --sample`)
| # | change (general rule) | status acc | step F1 | exact |
|---|---|---|---|---|
| 0 | first planner | 100% | 88.1% | 12/32 |
| 1 | a read-back of stored fields implies the record exists (was: SQL-snapshot omission overrode log readbacks → "ABSENT" for analytics/support); keep every event name in a merged log trace (contact_query + deletion_marker_check); JSON store snapshots enumerate their store (empty `customer_profiles` snapshot proves the account absent — T11 case_31 evidence_51) | 100% | 94.0% | 16/32 |
| 2 | **staleness**: a key-linked resource whose latest observation predates `requested_at` belongs to an earlier episode (case_17's March account/tombstone of the same person, case_07's February object) — drop; **violation-aware batch attribution**: completed policy actions go to requests whose own records are clean | 100% | 96.3% | 20/32 |
| 3 | tried: pick the batch account closest in time to reported_at → 94.7%, reverted (no evidence for it) | | | |
| 4 | violation preference also for accounts, but only among records the case's own evidence observes (a clean case needs them as proof); COMPLETE plans drop a verification they cannot prove | 100% | 96.3% | 20/32 |

Overfitting check: every rule is stated in terms of policy/evidence semantics (time cutoff, what a
readback proves, what a snapshot enumerates, batch semantics) — no case ids, no resource ids.

## Open questions resolved
- **T10** (case_12 analytics_9914 → DELETE): the record was read by `contact_lookup_completed` with
  `email_opt_in`, i.e. it is a contact/marketing record, not an analytics profile. Same for case_10
  support_002 (`contact_query` on `legacy_contact_index`). Rule: contact lookups → SECONDARY_IDENTIFIER,
  `email_opt_in` → MARKETING_PROFILE, both DELETE.
- **T11** evidence tie-break: the observation that decides the state = latest by observed time; ties →
  evidence files over activity rows, explicit over omission, then latest captured. Measured: evidence_id is
  right on 100% of matched steps (step F1 with and without evidence_id are equal, 96.3%).
- **T8**: verification = account VERIFY_ABSENT always + VERIFY_PRESENT for retained records + the target
  state of every other linked resource (compliant ones included, evidence "" in INCOMPLETE cases).
  Confirmed on all 32 samples.

## Remaining sample misses (all explained)
- 8 steps unreachable before audit_at (see Phase 1 handoff).
- Account ties within one batch and one evidence file: case_22, case_23, case_27↔28, case_32.

## Full run (deterministic)
518 rows, valid; COMPLETE 152 / INCOMPLETE 356 / NOT_APPLICABLE 10; two runs byte-identical
(md5 105d9ecd…). 232 cases carry an uncertainty flag (217 batch ties, 16 hash-only links, 11 without an
account).

## Next steps (Phase 3)
Groq client with token bucket + retries, LLM intent (batched 25), investigator over tools, reconcile.
Route only a small, high-value subset (budget), cache everything.
