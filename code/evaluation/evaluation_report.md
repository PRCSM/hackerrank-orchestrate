# Evaluation report — Ghost Data Still Exists

All numbers are reproducible:

```bash
python3 code/main.py --no-llm --sample          # -> code/evaluation/sample_predictions.csv
python3 code/evaluation/main.py --pred code/evaluation/sample_predictions.csv --diff
python3 code/evaluation/main.py --custom --diff # hand-built cases
python3 -m pytest -q code/tests                 # 23 tests, includes the regression guards below
```

Metric definitions (code/evaluation/main.py):
**status accuracy** = share of cases with the right deletion_status;
**step-level P/R/F1** = micro precision/recall/F1 over the multiset of
`(phase, system_id, resource_id, operation, state, evidence_id)` tuples (also reported without evidence_id);
**field-level accuracy** = per field, share of gold steps whose predicted step at the same position has the
same value (positional, so one missing step shifts the rest of that case);
**sequence accuracy** = share of cases whose whole steps array is identical (status included).

## 1. Headline: deterministic-only vs agent + reconcile (32 labelled samples)

| metric | deterministic only (`--no-llm`) | agent + reconcile |
|---|---|---|
| status accuracy | **100.0%** | 100.0% (measured with a scripted investigator, see note) |
| step P / R / F1 | **97.3% / 93.3% / 95.2%** | 97.3% / 93.3% / 95.2% |
| sequence accuracy | **62.5% (20/32)** | 62.5% (20/32) |
| cases routed to investigator | 13 flagged (12 within budget) | same |

Note — no live model run was possible in the build environment: no `GROQ_API_KEY` was provided and the
session's network policy blocks `api.groq.com`. The agent column is therefore measured with a scripted
transport whose investigator proposes an invented resource for every routed case
(`tests/test_end_to_end.py::test_agent_mode_never_scores_below_baseline`): reconcile rejected every
ungrounded proposal, so agent+reconcile = baseline. That proves the safety property (the agent cannot make the
answer worse) but not an improvement; run `python3 code/main.py --sample` with a key to measure the gain.
The only place the agent *can* change an answer is an uncertain batch tie (section 4), where the baseline is
right about half the time.

## 2. Full metrics (deterministic, final code)

| metric | value |
|---|---|
| cases | 32 |
| status accuracy | 100.0% |
| step precision / recall / F1 | 97.3% / 93.3% / 95.2% |
| step P / R / F1 ignoring evidence_id | 97.3% / 93.3% / 95.2% |
| sequence (exact case) accuracy | 62.5% (20/32) |
| field accuracy: phase | 95.9% |
| field accuracy: system_id | 89.6% |
| field accuracy: resource_id | 87.0% |
| field accuracy: operation | 89.6% |
| field accuracy: state | 89.6% |
| field accuracy: evidence_id | 95.3% |

Status confusion (rows = gold, columns = predicted):

| gold \ pred | COMPLETE | INCOMPLETE | NOT_APPLICABLE |
|---|---|---|---|
| COMPLETE | 10 | 0 | 0 |
| INCOMPLETE | 0 | 22 | 0 |
| NOT_APPLICABLE | 0 | 0 | 0 |


## 3. Per-failure table (every remaining sample miss)

| case | missing (gold) | extra (ours) | root cause | fixable? |
|---|---|---|---|---|
| case_04 | VERIFY_ABSENT account_004 (evidence_54) | – | account_004 appears in no participant file at all | no |
| case_09, 18, 20, 29, 30 | VERIFY_PRESENT audit_record_4902 / 6645 / 5569 / 3954 / 1707 | – | the retained security record is in no pre-audit evidence and no activity row | no |
| case_23, 24 | VERIFY_PRESENT audit_record_4128 / 7095 | – | only in evidence_02, captured 09-17 after audit_at 09-15 (T1 forbids it) | no (by rule) |
| case_22 | account_4990 | account_6829 | two requests share one deletion batch; neither account is observed anywhere | tie (agent) |
| case_23 | account_410 | account_294 | same — batch tie | tie (agent) |
| case_27 ↔ case_28 | account_3866 / account_5482 | swapped | both 404 in the same export (evidence_92), same batch | tie (agent) |
| case_32 | account_8858 | account_2381 | account_2381 belongs to case_38 but that is only visible in evidence after case_32's audit | tie (agent) |

Ceiling with participant data before audit_at: 8 of 193 gold steps are unreachable → recall ≤ 95.9%.
We reach 93.3% (180/193); the remaining gap is the five tied accounts (5 missing + 5 extra steps).

## 4. Error analysis: what the uncertainty router sends to the agent

On the 518 evaluation cases the baseline flags 235 cases: 217 batch ties (a 404 account or a fully
anonymised record that only the deletion-worker batch connects to the request, with another request in the
same batch and evidence file), 16 links that rest only on a hashed identifier, and 11 cases without any
account resource (10 hand-crafted cases have no deletion-worker batch at all). The default free-tier budget
investigates the 12 highest-severity ones.

## 5. Custom evaluation cases (hand-built dataset, `code/evaluation/custom_cases/`)

A separate miniature dataset (identities, audit log, manifest, a readback log, two JSON snapshot files,
activity, requests and hand-written `expected_output.csv`) exercises the general rules on identities the
pipeline has never seen:

| case | what it tests | expected |
|---|---|---|
| case_c1 | **(a) lookalike decoy + historical email + claim vs proof**: `maria.lopez.jr@` has a live search hit that must not be attributed; `mlopez.old@` (pre-change email) still has a search hit although an activity claims it was deleted | INCOMPLETE: REMAINING search_102 (evidence_c1), DELETE, verify account_101 + search_102 absent; search_101 absent from output |
| case_c2 | **(d) non-English non-deletion**: Japanese "send me a copy … please do not delete" | NOT_APPLICABLE, `[]` |
| case_c3 | **(c) uppercase + hash-only match**: account looked up as `ANNA.BERG@EXAMPLE.COM`; analytics row exposes only the sha256 email hash | INCOMPLETE: EXPOSED_PII analytics_104 → ANONYMIZE; verify account absent, audit record present |
| case_c4 | **(b) evidence after audit_at**: a 05-20 snapshot shows the account ACTIVE, audit_at is 05-12; the 05-10 readback says 404 | COMPLETE: VERIFY_ABSENT account_105 citing evidence_c1 (not evidence_c2) |
| case_c5 | **restore risk + tombstone with PII** (German request text) | INCOMPLETE: EXPOSED_PII tombstone_106, RESTORE_RISK backup_106, BLOCK_RESTORE before REDACT, three verifications |
| case_c6 | Spanish marketing opt-out while keeping the account | NOT_APPLICABLE |

Result (final code):

| metric | value |
|---|---|
| cases | 6 |
| status accuracy | 100.0% |
| step precision / recall / F1 | 100.0% / 100.0% / 100.0% |
| step P / R / F1 ignoring evidence_id | 100.0% / 100.0% / 100.0% |
| sequence (exact case) accuracy | 100.0% (6/6) |
| field accuracy: phase | 100.0% |
| field accuracy: system_id | 100.0% |
| field accuracy: resource_id | 100.0% |
| field accuracy: operation | 100.0% |
| field accuracy: state | 100.0% |
| field accuracy: evidence_id | 100.0% |

Status confusion (rows = gold, columns = predicted):

| gold \ pred | COMPLETE | INCOMPLETE | NOT_APPLICABLE |
|---|---|---|---|
| COMPLETE | 1 | 0 | 0 |
| INCOMPLETE | 0 | 3 | 0 |
| NOT_APPLICABLE | 0 | 0 | 2 |

First run of these cases (before the fixes in section 6, items 9–10): status 66.7%, step F1 69.2%, 3/6 exact.

## 6. Failures found and improvements made (chronological)

| # | failure found | fix (general rule) | metric delta (samples unless noted) |
|---|---|---|---|
| 1 | Only 68.5% of gold resources are reachable by identifiers: 404 accounts and anonymised analytics carry no identifier | deletion-worker batch inference around reported_at | discovery recall 68.5% → 85.6% (26 false links) |
| 2 | Batch inference too greedy (5 h window, grouped by identical claimant sets) | phrase-specific windows, evidence-file proximity, component-wide ordered matching | recall 86.5%, false links 26 → 8 |
| 3 | Legal-hold PDF attached tombstone_17 to six users through a bare `user_17` alias | user-id aliases only trusted on pages produced by the resource's own system | 6 false links removed |
| 4 | First planner: SQL-snapshot omission overrode log readbacks (analytics/support marked ABSENT) | a read-back of stored fields implies the record exists | step F1 88.1% → 94.0% (with 5, 6) |
| 5 | Contact lookups (analytics_9914, support_002) treated as analytics/support messages | merged log traces keep every event; contact lookups → SECONDARY_IDENTIFIER, `email_opt_in` → MARKETING_PROFILE (DELETE) — resolves T10 | included above |
| 6 | Empty `customer_profiles` snapshot not used as proof of absence (case_31 cites evidence_51) | JSON store snapshots enumerate their store | included above; T11 evidence choice now 100% on matched steps |
| 7 | Same person deleted twice (case_17 vs case_46; case_07's February attachment) produced earlier-episode records | key links whose latest observation predates requested_at are stale | F1 94.0% → 96.3%, exact 16 → 20 |
| 8 | Validator rejected case_532: COMPLETE answer with an account no evidence proves | clean requests get first pick of batch records their own evidence observes; COMPLETE plans drop unprovable checks | full run valid; samples unchanged |
| – | Tried: assign the batch account closest in time to reported_at | measured worse, reverted | F1 96.3% → 94.7% → reverted |
| 9 | Custom case_c2: Japanese "削除はしないでください" (do not delete) classified as deletion | multilingual negation patterns (JA/KO/ES/PT/FR/DE) | fixed together with 10 ↓ |
| 10 | Custom case_c5: German "löschen" not recognised | DE/IT/NL deletion verbs; LLM wins only when rules find no pattern | custom status 66.7% → 100%, F1 69.2% → 100% (9+10; the third custom failure was my non-numeric user ids in the custom data, fixed in the data) |
| 11 | case_242 had no account: its batch ran up to +48 min after reported_at | profile-submission window widened to +1 h | samples unchanged; output unchanged (tie remains) |
| 12 | Scorer bug: step tuples were intersected across all cases, so two cases that swapped accounts counted each other's step as a hit | per-case matching in `evaluation/main.py::score` | reported F1 96.3% → **95.2%** (true value; rows 1–8 above were measured with the old scorer and are ~1 pt optimistic) |

## 7. Validation of the submitted output.csv

`validate_output` (code/validate/validator.py) on the final `./output.csv`: exact header, 518 rows, sorted
by case id, ids one-to-one with deletion_requests.csv, valid JSON, consecutive step numbers, allowed
(phase, operation, state) only, no evidence captured after audit_at, empty evidence_id on future work,
COMPLETE = verification only, retained categories never deleted — **0 problems**. Two runs are byte-identical.
Distribution: COMPLETE 152, INCOMPLETE 356, NOT_APPLICABLE 10.
