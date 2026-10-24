# Handoff — Phase 5: evaluation and reports

## Files
- `evaluation/evaluation_report.md` — side-by-side deterministic vs agent+reconcile, full metrics, confusion
  matrix, per-failure table with root causes, custom cases, chronological failures/improvements, output
  validation.
- `evaluation/usage_report.md` — actual usage of the final run (0 calls: no key, Groq blocked) and a
  projection from measured prompt sizes at Groq prices looked up 2026-10-24.
- `evaluation/custom_cases/data/*` + `expected_output.csv` — miniature dataset, 6 cases.
- `evaluation/main.py --custom` — runs and scores the custom cases; `run_custom()` is also a pytest guard.
- `storage/usage.py::projected_cost` — the run summary prints the paid-tier projection from `PRICE_PER_MTOK`.

## Numbers (final code)
- Samples: status 100%, step P/R/F1 97.3 / 93.3 / 95.2%, sequence 62.5% (20/32), evidence_id right on every
  matched step. Recall ceiling 95.9% (8 unreachable gold steps).
- Custom: 6/6 exact (first run 3/6).

## Decisions
- **Scorer fixed to match steps within a case.** The old global multiset intersection credited swapped
  accounts as hits (+1.1 pt F1). Earlier phase numbers in handoffs 1–2 were computed with the old scorer.
- Custom data uses numeric `user_9xx` ids, because the identity key grammar is `user_<digits>` like the real data.

## Next (Phase 6)
READMEs, code.zip, interview prep, final log check, push.
