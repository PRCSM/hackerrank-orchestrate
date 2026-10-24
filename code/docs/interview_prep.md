# Interview prep — Ghost Data Still Exists

## 60-second pitch
A workflow saying "Complete" is a claim. My agent treats every status and activity result as a claim, and
proves state only from readbacks: a 404, a null field, `enabled=false`, `restore_allowed=false`. It runs as a
tool-using investigator on top of a deterministic evidence graph. Code ingests all six evidence formats
(SQL replicas, JSON, six log styles, handover text, OCR'd screenshots, PDFs) into one observation model with a
hard audit_at cutoff. It then resolves current and historical identities by exact match, rebuilds each copy's
state at audit time, applies `deletion_rules.csv`, and emits the ordered steps, all deterministic and
validated. The LLM is used where language and judgment are needed: intent in any language, and breaking
linkage ties the evidence leaves open. It can only change the answer with ids a tool returned, and a
reconciler keeps the deterministic baseline otherwise. On the 32 samples: 100% status accuracy, 95.2% step
F1, 20/32 exact, against a reachable-recall ceiling of 95.9%.

## Architecture walkthrough: one case end to end (case_25)
1. `main.py` loads the requests. `orchestrator.Orchestrator.run` classifies intent:
   `agent/intent_rules.classify` matches "been removed" (the LLM path is `agent/intent.classify_requests`).
2. `graph/identity.IdentityResolver.resolve("s.yoon04@gmail.com", audit_at)` returns user_25 with all its
   emails, phones, hashes and user id.
3. `graph/linkage.Linker.link_all`:
   - key links: `audit_record_451` carries `user_25` in evidence_92.
   - batch links: `account_3136` (activity_3597, "submitted primary profile", 1h12m before reported_at) and
     `analytics_1895` (activity_3602, "Policy action completed"). Both are observed in evidence_92, the case's
     own evidence file. Both are flagged uncertain because case_26 is in the same batch.
4. `graph/state.resource_state_at` gives: account_3136 presence=False (404, evidence_92, although
   activity_3597 says FAILED); analytics_1895 attributable=False (all identifier fields null);
   audit_record_451 present.
5. `policy/planner.assess` + `build_plan`: no finding, so the case is COMPLETE. The verifications cite the
   deciding observation, evidence_92.
6. `validate/validator.validate_case` checks the result. `storage/writer.write_output` writes compact JSON
   with sorted keys. `storage/trace` explains every step.

## Why agent + deterministic gate (not pure rules, not pure LLM)
- **Pure LLM** would have to read about 1.2 MB of evidence per case, or rely on retrieval it can't verify.
  It would also have to hold an exact time cutoff, exact equality on lookalike emails, and a 15-operation
  vocabulary with strict ordering. Every one of those is a deterministic check that code gets 100% right and
  an LLM gets right "usually". The validator would reject LLM slips, so the LLM would just add cost.
- **Pure rules** are exact on intent (32/32 samples, and the 10 non-deletion test texts match the 10
  CLASSIFY_REQUEST "non-deletion" activities) and
  on states. They cannot know which of two requests in the same deletion batch owns an identifier-less 404
  account. That is 217 of the 518 cases, and the baseline is right about half the time on these ties.
- **The split**: rules provide the 95.2% F1 floor, and the agent can only act on flagged ties, with grounded
  ids. The guarantee is tested: with a junk-proposing investigator, agent+reconcile scores exactly the
  baseline (`test_agent_mode_never_scores_below_baseline`).
- **Cost**: routing 12 of 235 flagged cases plus batched intent comes to about 160K tokens and about $0.03 per
  full run at paid prices, or $0 on the free tier.

## The 5 hardest traps and how the code handles them
1. **Claims vs proof** (case_25: delete FAILED but readback 404). `ingest/parse_activity.activity_observations`
   creates state only from checked facts ("remains enabled", "can still be restored", "repopulated").
   `observation._presence` reads http_status / rows / result / available.
2. **Time cutoff** (a 09-17 bundle shows ACTIVE; the audit is 09-15). `graph/evidence_graph.usable` requires
   captured_at ≤ audit_at and observed_at ≤ audit_at. Tools inherit the cutoff, and the validator rejects
   any cited evidence captured after audit_at.
3. **Identifier-less records** (404 accounts, anonymized analytics). `graph/linkage.Linker._batch` and
   `_assign` attribute them by deletion-batch semantics, then by evidence-file proximity, then by whether the
   case is clean. Ties are flagged `uncertain`.
4. **Lookalikes, case noise, hashes.** `ingest/observation.candidate_keys` normalizes. `graph/identity`
   adds sha256(lower(email)) for every historical email (case_12: analytics_9914 is found only via the hash
   of an old email). Matching is exact only, so `g.silva76.jr` stays a different person.
5. **Same record, different meaning.** A contact lookup (analytics_9914, support_002) is a
   SECONDARY_IDENTIFIER/marketing record, so it must be DELETED, not anonymized or redacted
   (`observation.CATEGORY_HINTS`, `state.infer_category`). Tombstones keep `subject_reference` but no direct
   identifiers (`observation.DIRECT_IDENTIFIER_FIELDS` vs `STABLE_REFERENCE_FIELDS`). Replica-only rows are
   live copies (`parse_sql`).

## 3 real failures found and fixed (with deltas)
1. **SQL omission vs readback.** A snapshot that didn't list a record was overriding a newer log readback,
   so present analytics were reported ABSENT. Fix: a field readback implies existence; omission is only used
   when nothing else speaks to presence. Step F1 88.1% → 94.0% (old scorer).
2. **Same person, two episodes.** case_17 (September) inherited case_46's March account and tombstone. Fix:
   key links not observed since `requested_at` are stale. Combined with clean-first batch attribution:
   94.0% → 96.3% (old scorer), exact 16 → 20.
3. **My own scorer was wrong.** It intersected step tuples across cases, so two cases that swapped accounts
   counted each other's steps as hits. Fix: per-case matching. The reported F1 fell from 96.3% to the true
   95.2%. I report the honest number. (Also: custom cases found the Japanese "削除はしないで" negation and the
   unknown German "löschen". Custom status went 66.7% → 100%.)

## Limitations and next steps
- No live LLM numbers: the build environment had no Groq key and blocked the Groq hosts. Next step: run
  `--sample` with a key, then compare agent+reconcile against the 95.2% baseline per routed case.
- Batch ties are a coin flip. Better signals to try: order consistency across a whole batch (assign
  jointly with a min-cost matching over all resource types at once), and agent review of request text
  ("…identifiable analytics…").
- Unreachable gold (security records that appear in no pre-audit evidence) caps recall at 95.9%.
- OCR state rules for screenshots are keyword-based. A local vision model would make them structural.

## Production monitoring ideas
- Per-run drift dashboard: share of cases COMPLETE/INCOMPLETE, flagged-case rate, batch-tie rate, and the
  share of findings by category. Alert on jumps.
- Claim-vs-proof gap: count cases where `reported_status=COMPLETE` but the audit finds violations, by
  system. That is the real KPI for the deletion workflow.
- Evidence freshness SLO: age of the deciding observation at audit time. Stale proofs mean missing
  readbacks.
- LLM guardrail metrics: ungrounded-id drop rate, reconcile rejection rate, intent disagreements, 429s and
  quota fallbacks, and tokens per case.
- Golden-set regression: the samples and custom cases run in CI (`pytest`) with metric floors.

## 20 likely judge questions
1. **Why not trust `reported_status`?** It's a workflow claim. case_24 is "COMPLETE" while the account still
   returns 200 with the email. State comes only from readbacks.
2. **How do you enforce the time cutoff?** `evidence_graph.usable`: file captured_at ≤ audit_at and
   observed_at ≤ audit_at, for every query, tool and citation. The validator double-checks citations.
3. **How do you handle historical emails?** Current snapshot plus audit-log old/new values up to audit_at,
   plus sha256 of every email. Exact matching only.
4. **How do you link a 404 account with no identifier?** Through the deletion-worker batch that ran just
   before `reported_at`, the case's own evidence file observing it, and whether competing cases are clean.
   Ties are flagged and routed.
5. **What does the LLM actually do?** Intent in batches of 25 (gpt-oss-20b), and investigating flagged ties
   with 6 tools (gpt-oss-120b). It never writes steps.
6. **How do you stop hallucinated resources?** Every proposed id must have appeared in a tool result, and its
   system must match its prefix. Reconcile accepts only the case's own batch candidates or identifier-linked
   records.
7. **Prompt injection in request_text?** The prompts declare it untrusted data. The LLM can override the
   intent rules only when no rule pattern matched, and is tested with an injected text.
8. **Why is NOT_APPLICABLE `[]`?** No deletion is authorized, so there are no deletion steps (10 cases:
   533–542).
9. **How do you choose evidence_id when several files qualify?** The latest observation by observed time.
   At ties, files beat activity rows and explicit observations beat snapshot omission. evidence_id is right on
   100% of matched sample steps.
10. **Replication drift?** Every replica row is a copy. 13 replica-only rows were found (e.g. account_994),
    and they are reported as remaining records, as in the samples.
11. **Tombstones?** REDACT: `subject_reference` may stay; name, email, phone and hash must be null.
12. **Why does analytics with only `customer_reference` count as exposed?** Anonymization means it can't be
    linked to the user; a user id still links it. Support and tombstones keep the reference (redaction).
13. **Why DELETE analytics_9914 instead of ANONYMIZE?** It was read by a contact lookup with
    `email_opt_in`, so it is a contact/marketing record, and rule_02/rule_10 say DELETE (T10).
14. **Remediation order?** The table order: DISABLE_DATA_FLOW → BLOCK_RESTORE → DELETE → REDACT → ANONYMIZE.
    Recreation paths are stopped before the copies are touched.
15. **Why verify already-compliant resources in INCOMPLETE cases?** The samples do it (T8): the final-state
    checks cover every relevant record, including the account and retained audit logs.
16. **What breaks your score most?** Batch ties (5 sample steps) and unreachable gold (8 steps).
17. **Determinism?** No randomness. Inputs are sorted everywhere, JSON is compact with sorted keys, and two
    runs give identical md5. LLM results are cached by prompt hash.
18. **Free-tier limits?** A token bucket per model, tightened from `x-ratelimit-*` headers, honoring
    `retry-after`. A daily quota switches to qwen, then to deterministic. Routing is capped at 12 cases.
19. **What if one case crashes?** It is caught per case, falls back to deterministic or NOT_APPLICABLE, and
    is recorded in the trace. The run continues.
20. **Did you overfit to the samples?** Every rule is stated as evidence semantics: no case ids, no resource
    ids, no sample answers in logic. Custom unseen identities score 6/6, and one rule that helped only by
    coincidence (closest-to-reported_at) was rejected because it had no evidence behind it.
