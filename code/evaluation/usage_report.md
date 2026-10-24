# Usage report — final full-dataset run

## The run that produced `output.csv`

| item | value |
|---|---|
| command | `python3 code/main.py` (518 cases) |
| mode | deterministic: no `GROQ_API_KEY` in the build environment, and the session network policy blocks `api.groq.com` |
| provider | Groq, via its OpenAI-compatible API (`openai` SDK, `base_url=https://api.groq.com/openai/v1`); configured but not reachable |
| result | 518 rows, COMPLETE 152 / INCOMPLETE 356 / NOT_APPLICABLE 10, validator 0 problems |

### Actual usage (from `code/.cache/usage.jsonl`, written from each response's `usage` field)

| model | role | calls | cache hits | input tokens | output tokens | total tokens |
|---|---|---|---|---|---|---|
| openai/gpt-oss-20b | intent classifier (batches of 25) | 0 | 0 | 0 | 0 | 0 |
| openai/gpt-oss-120b | investigator (tool-calling, ≤6 turns) | 0 | 0 | 0 | 0 | 0 |
| qwen/qwen3.8-27b | fallback after a daily-quota 429 | 0 | 0 | 0 | 0 | 0 |
| **total** | | **0** | **0** | **0** | **0** | **0** |

- Average tokens per request: **0** (0 / 518).
- Actual cost: **$0.00** (no model calls; on Groq's free tier the cost would also be $0).
- Cases routed by the uncertainty router: **235 flagged**, of which **12** fit the default investigation
  budget (`GDSE_MAX_INVESTIGATIONS=12`). Reasons: 217 batch-inferred resource with an equally plausible rival
  request, 16 resource linked only through a hashed identifier, 11 no account resource found (a case can
  carry several). In this run none were investigated, because no model was reachable; their deterministic
  plans were written. They are listed per case in `code/traces/<case_id>.json` (`router_flags` /
  `uncertainty`).
- Cases that fell back to deterministic because of quota: none (no quota was consumed).

To produce real numbers: put the key in `.env` (see `.env.example`) and rerun `python3 code/main.py`. The
ledger, cache and this table's inputs are filled automatically. The summary is printed at the end of the run
(`usage <model>: calls=…, input_tokens=…, output_tokens=…, cache_hits=…`).

## Projected usage and cost of an LLM-enabled run (not measured)

These estimates come from the actual prompt files and data: characters ÷ 4, measured on the real batches and
on a real investigator transcript skeleton (case_210). They are not API counts.

| model | calls | input tokens | output tokens | basis |
|---|---|---|---|---|
| openai/gpt-oss-20b | 21 | ≈40,500 | ≈13,000 | 518 texts / 25 per batch; prompt 1,930 tokens avg per batch; ~25 output tokens per case |
| openai/gpt-oss-120b | ≈60 | ≈99,000 | ≈9,000 | 12 cases × ≈5 turns; system prompt + tool schemas 1,009 tokens, case message 136, tool results 20–260 each (capped at 1,500 chars) |
| **total** | **≈81** | **≈139,500** | **≈22,000** | **≈161,500 tokens, ≈312 tokens per request (518)** |

Both models stay under the assumed 200K tokens/day free-tier budget, so a full run fits in one day. A
rerun costs 0 tokens: intent batches and investigations are cached by prompt hash and inputs
(`code/.cache/llm/`), and each cache hit is logged with 0 tokens.

### Projected paid-tier cost

Groq on-demand prices, looked up on **2026-10-24**. groq.com itself was unreachable from the build
environment, so these come from public pricing aggregators that cite Groq's model docs:
[morphllm.com/groq-pricing](https://www.morphllm.com/groq-pricing) and
[flexprice.io/pricing-index/groq](https://flexprice.io/pricing-index/groq).

| model | $ / 1M input | $ / 1M output | projected cost |
|---|---|---|---|
| openai/gpt-oss-20b | 0.075 | 0.30 | 40.5K × 0.075 + 13K × 0.30 ≈ **$0.0069** |
| openai/gpt-oss-120b | 0.15 | 0.60 | 99K × 0.15 + 9K × 0.60 ≈ **$0.0203** |
| qwen/qwen3.8-27b (fallback only) | 0.80 (sources disagree: 0.60–0.80) | 4.00 (3.00–4.00) | $0 unless a quota is hit |
| **total** | | | **≈ $0.027 per full run, ≈ $0.000053 per request** |

The price table lives in `code/config.py` (`PRICE_PER_MTOK`) so it can be updated in one place.
