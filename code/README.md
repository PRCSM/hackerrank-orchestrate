# Ghost Data Still Exists — deletion-audit agent (code)

A tool-using investigator agent on top of a deterministic evidence graph. Code does the parts that must be
exact: the audit_at cutoff, identity matching, state reconstruction, policy, ordering, serialization and
validation. The LLM does the parts that need language and judgment: intent in any language, and breaking
linkage ties the evidence leaves open. Every LLM proposal is checked against tool results and reconciled
with the deterministic baseline before anything is written.

## Setup

```bash
python3 --version             # 3.11+ (developed on 3.13)
pip install openai pydantic pytest
# OCR / PDF text for screenshots and documents
sudo apt-get install -y tesseract-ocr poppler-utils      # macOS: brew install tesseract poppler
cp .env.example .env          # optional: add GROQ_API_KEY=...  (.env is git-ignored)
```

Environment variables (all optional, read only from the environment or an uncommitted `.env`):

| variable | default | meaning |
|---|---|---|
| `GROQ_API_KEY` | – | enables the LLM layer; without it the run is fully deterministic |
| `GDSE_INTENT_MODEL` | `openai/gpt-oss-20b` | intent classifier |
| `GDSE_INVESTIGATOR_MODEL` | `openai/gpt-oss-120b` | tool-calling investigator |
| `GDSE_FALLBACK_MODEL` | `qwen/qwen3.8-27b` | used after a daily-quota 429 |
| `GDSE_MAX_INVESTIGATIONS` | `12` | uncertain cases investigated per run |
| `GDSE_RPM` / `GDSE_TPM` | `30` / `8000` | starting rate limits (tightened from `x-ratelimit-*` headers) |
| `GDSE_DATASET_DIR`, `GDSE_OUTPUT_PATH`, `GDSE_CACHE_DIR`, `GDSE_TRACE_DIR` | repo defaults | paths |

## Run

```bash
python3 code/main.py                      # all 518 cases -> ./output.csv (LLM if GROQ_API_KEY is set)
python3 code/main.py --no-llm             # deterministic only
python3 code/main.py --sample             # 32 labelled samples -> code/evaluation/sample_predictions.csv
python3 code/main.py --cases case_33,case_210
python3 code/main.py --no-resume          # ignore cached LLM results
python3 code/evaluation/main.py --pred code/evaluation/sample_predictions.csv --diff
python3 code/evaluation/main.py --custom --diff
python3 -m pytest -q code/tests
```

A full deterministic run takes about 3 s, plus about 20 s of OCR on the first run; OCR results are cached in
`code/.cache/media/`. Per-case traces are written to `code/traces/<case_id>.json`, and a few examples are
committed in `code/traces/examples/`.

## Architecture

```
dataset/ ──► ingest/  (one parser per format, one semantics layer)
               parse_sql  parse_json  parse_logs  parse_text  parse_media(OCR/PDF)  parse_activity
                     └──────────────► Observation{evidence, time, system, resource, keys,
                                                  presence, identifiable, references_user,
                                                  flow_active, restore_allowed, category}
             graph/
               evidence_graph  index by resource / identity key, hard audit_at cutoff,
                               absence-by-enumeration for snapshots that list a store
               identity        exact, case-insensitive email -> user + all emails/phones/sha256 hashes
               linkage         key links · recreation links · deletion-batch inference (+ uncertainty)
               state           latest usable fact per dimension + the evidence that proves it
             policy/
               rules           deletion_rules.csv -> DELETE | REDACT | ANONYMIZE | RETAIN | BLOCK | DISABLE
               planner         FINDING / REMEDIATION / VERIFICATION, table order, status
             agent/
               intent(+_rules) batched LLM intent, multilingual rule fallback
               tools           6 case-bound tools (≤1,500-char results)
               investigator    ≤6-turn tool loop, grounded JSON proposal
               llm_client      Groq/OpenAI SDK, token bucket, retries, quota fallback
               prompts/*.md
             validate/
               reconcile       baseline wins unless the agent resolves an uncertain tie with grounded ids
               validator       last gate: schema, vocabulary, ordering, evidence time, policy
             storage/          writer (deterministic CSV/JSON) · cache · usage ledger · trace
             audit.py          deterministic pipeline   orchestrator.py  routing + fallbacks   main.py  CLI
```

How one case flows through the pipeline (case_25, `s.yoon04@gmail.com`, audit 2026-09-15T12:00):
1. **intent**: "...whether the account and identifiable analytics data have already been removed" matches
   the rule `been removed`, so deletion is authorized.
2. **identity**: the email resolves to `user_25`. Its keys are the email, the phone, the sha256 hash and `user_25`.
3. **linkage**: `audit_record_451` carries `user_25` in evidence_92 (key link). `account_3136` and
   `analytics_1895` carry no identifier anywhere. The deletion-worker batch before reported_at
   (activity_3597, activity_3602) handles them, and the case's own evidence file evidence_92 observes them
   (batch link, flagged uncertain because case_26 shares the batch).
4. **state at audit_at**: account_3136 returns 404 in evidence_92, even though activity_3597 claims FAILED.
   analytics_1895 has all identifier fields null. audit_record_451 is present with retention until 2031.
5. **policy and plan**: no violation, so the case is COMPLETE with VERIFY_RECORD_ABSENT, VERIFY_RECORD_PRESENT
   and VERIFY_RECORD_ANONYMIZED, each citing evidence_92.
6. **validate, then write**: compact JSON with sorted keys.

## Design decisions (why)

- **State comes from readbacks, never from claims.** `reported_status`, `reported_result` and workflow
  messages never set a state. Only readbacks do (404 / rows=0 / NOT_FOUND, null or `[REDACTED]` fields,
  `enabled`, `restore_allowed`).
- **Hard time cutoff at the observation level.** An observation is usable only if its file was captured
  and its fact was observed at or before audit_at. The same cutoff applies to tools, so the agent cannot
  see the future either.
- **Exact identifier matching.** The dataset contains lookalikes (`name.jr@...`). Emails are normalized
  by strip and lower-case, phones by digits only, and hashes are sha256(lower(email)).
- **Batch inference** is the only way to connect 404 accounts and anonymized records, which carry no
  identifier. It is flagged uncertain whenever another request competes for the same record, and these
  flags drive the agent router.
- **Staleness.** A key-linked record not observed since the request was made belongs to an earlier
  deletion of the same person. This happens in the data: the same people appear in March and in September.
- **Deterministic gate around the LLM.** The baseline is already exact on intent and states. The agent can
  only resolve ties, and only with ids a tool returned in that conversation.
- **`code/storage/` instead of `code/io/`.** A package named `io` on `sys.path` would shadow the
  standard-library module.

More detail: `code/docs/handoff_phase1..5.md`, `code/evaluation/evaluation_report.md`,
`code/evaluation/usage_report.md`, `code/docs/interview_prep.md`.
