# Handoff — Phase 3: agent layer (Groq, OpenAI-compatible)

## Files created / changed
- `agent/llm_client.py` — `GroqClient.chat(model, messages, purpose, case_id, tools, json_mode, max_tokens)`:
  `openai` SDK with `base_url=https://api.groq.com/openai/v1`, `GROQ_API_KEY`; per-model token bucket
  (RPM/TPM, tightened from `x-ratelimit-limit-*` headers); 429/5xx retry with `retry-after` or exponential
  backoff (max 3); a 429 naming a daily quota raises `QuotaExhausted`; usage recorded from `usage`.
- `agent/intent.py` — batched (25) JSON-mode classification with `openai/gpt-oss-20b`; one repair turn on
  malformed JSON; `qwen/qwen3.8-27b` on daily quota; rules on failure. Disagreement policy: rules win when
  an explicit pattern matched; LLM wins when the rules only defaulted (unseen phrasing/language).
- `agent/intent_rules.py` — deterministic multilingual gate (EN/ES/PT/FR/JA/KO).
- `agent/tools.py` — `CaseTools` bound to one case/audit_at: `resolve_identity`, `find_resources`,
  `batch_candidates`, `resource_state`, `resource_activity`, `get_rule`; results ≤1,500 chars; records every
  id returned (grounding set).
- `agent/investigator.py` — ≤6-turn tool loop with `openai/gpt-oss-120b`, final JSON proposal; drops
  resources never returned by a tool or whose system_id does not match the id; abstains on malformed JSON
  after one repair.
- `agent/prompts/intent.md`, `agent/prompts/investigator.md` — role, untrusted-data rule, allowed values,
  JSON schema, abstain rules.
- `validate/reconcile.py` — baseline wins; the agent can only (a) swap an *uncertain* batch inference for
  another of this request's own batch candidates of the same type, (b) add a resource that carries the
  subject's identifier before audit_at. Everything else is rejected and logged.
- `orchestrator.py` — routing by flag severity (identity ambiguity / no account 3, unproven COMPLETE /
  conflicting observations 2, hash-only link / batch tie 1), budget `GDSE_MAX_INVESTIGATIONS` (12 by default),
  cache per case+prompt+baseline (zero tokens on rerun), quota fallback chain, re-plan + validate after
  reconcile (invalid → baseline).
- `storage/cache.py`, `storage/usage.py`, `.env.example`, `config._load_dotenv`.
- Tests: `tests/test_agent_layer.py` (fake transport: token bucket, retry-after, daily quota, intent
  batching + override + cache hit, JSON repair, injection, grounding drop, reconcile rejection) and
  `tests/test_end_to_end.py::test_agent_mode_never_scores_below_baseline`. 22 tests pass.

## Blocking environment fact (documented, not worked around)
No `GROQ_API_KEY` is set in this session and the session network policy refuses `api.groq.com`,
`console.groq.com` and `groq.com` (proxy 403 / DNS). So:
- Model IDs could not be read from `GET /openai/v1/models`; the IDs you specified are in `config.py` and
  env-overridable. Pricing was taken from public aggregator pages (see usage_report.md).
- No live LLM run was possible; the agent layer is exercised end-to-end only with a scripted transport.
  The measured guarantee: with an abstaining or junk-proposing investigator, agent+reconcile scores exactly
  the deterministic baseline (status 100%, step F1 96.3% on samples). A real improvement number requires
  running `python3 code/main.py --sample` with a key.

## Intent gate on all 518 request texts (deterministic)
NOT_APPLICABLE = case_533 (phone update), 534 (promotional emails), 535 (portable download),
536 (login help, "do not close"), 537 (surname correction), 538 (marketing lists only),
539 (what data do you hold), 540 (invoices), 541 (email change), 542 (copy of data, "don't delete").
Every ES/PT/FR/JA/KO template classifies as deletion. Corroborated by the 10 CLASSIFY_REQUEST
"non-deletion" activities (e.g. account_2461 = elena.h3070 = case_541, account_228 = omar.nasser = case_542).

## Routing numbers (deterministic run)
232 of 518 cases carry a flag (217 batch ties, 16 hash-only links, 11 without an account); top 12 by severity
fit the default free-tier budget. Sample run: 13 flagged.

## Next steps (Phase 4)
Full run with resume; validate output.csv; 15 trace spot-checks (≥3 from case_33..50, ≥3 multilingual);
fix systemic issues; final ./output.csv.
