# Handoff — Phase 4: full run, validation, spot-checks

## Run
`python3 code/main.py` (no GROQ_API_KEY in this session → deterministic mode, announced on stdout).
- 518 rows → `./output.csv`; COMPLETE 152 / INCOMPLETE 356 / NOT_APPLICABLE 10.
- `validate_output`: header exact, 518 rows, sorted by numeric case id (input order), ids one-to-one,
  valid JSON, consecutive step_no, allowed (phase, operation, state), no evidence after audit_at,
  future work with "" — **0 problems**.
- Deterministic: two runs give md5 `105d9ecd969bb05dcf58c4470dda09ff`.
- Errors / fallbacks: 0 cases failed (the per-case try/except fallback was not triggered).
- Router: 235 flagged, 12 within the default investigation budget (not executed: no key).
- Quota fallback list: none (no LLM calls were made).

## Spot-checks (traces vs raw evidence)
| case | kind | check | verdict |
|---|---|---|---|
| case_33 | hand-crafted | export_1842 via statement PDF alias NST-2026-0718-1842 (activity_4009), captured 07-18 < audit 07-19 → REMAINING_RECORD evidence_18; export_1260 (June) dropped as stale | consistent |
| case_34 | hand-crafted | account_5762 shown on admin console 09-26 (evidence_04) → present; support_9538 linked by phone | consistent |
| case_35 | hand-crafted | search_428 g.silva76 hit; decoy search_1874 (g.silva76.jr) not attributed | consistent |
| case_39 | hand-crafted | data_flow_1121 "Continuous" on connector console (OCR `data_flow.1121`) → ACTIVE | consistent |
| case_45 | hand-crafted | audit_record_003 retained until 2027 (PDF), VERIFY_PRESENT evidence_37 | consistent |
| case_46 | hand-crafted | account_6523 404 (lookup trace 03-10), tombstone_17 reference-only registry page → COMPLETE | consistent |
| case_50 | hand-crafted | analytics_7438 profile page shows email → EXPOSED_PII | consistent |
| case_210 | PT | account batch-inferred (uncertain), search/support exposed in read replica 07-08 | consistent |
| case_522 | PT | support_8538 `message_pii_scan` direct identifier, account_1096 404 (correlation key) | verified in raw log |
| case_123 | FR | backup_8742 `restore_allowed=1` in inventory 08-17 → RESTORE_RISK | consistent |
| case_249 | PT | inventory 06-18 rows for account/analytics/search/support all carry the email | verified in raw SQL |
| case_84 | grid | historical email cchevalier408794 → account_2387 NOT_FOUND (diagnostic log) | consistent |
| case_97 | grid | analytics_9103 all-null readback, batch-inferred → ANONYMIZED | consistent |
| case_480 | grid | backup restore_allowed=false, connector disabled, support redacted → COMPLETE | consistent |
| case_334 | grid | search + support with email in inventory 05-04 → INCOMPLETE | consistent |

Systemic checks: replica-only rows (13 across SQL files, e.g. account_994 in the 09-21 read replica)
are treated as remaining copies (matches samples case_05/case_16).

## Systemic issue found and fixed
- case_242 had no account: its batch ran at 05:10–06:48 around reported_at 06:00; the profile-submission
  window ended at +15 min. Widened to +1 h (observed in data). Sample score unchanged; output unchanged
  (the account is still taken by a competing request in the same batch → inherent tie).

## Known gaps
- 10 hand-crafted cases have no deletion-worker batch at their reported_at, hence no account resource.
- Ambiguous batch ties remain the main error source (≈50% expected on those links).

## Next steps (Phase 5)
evaluation_report.md (+ ≥4 custom cases), usage_report.md, then packaging.
