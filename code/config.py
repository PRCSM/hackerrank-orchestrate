"""Central configuration: paths, model routing and limits.

Every value can be overridden through an environment variable so the same code
runs locally, in CI and in the grader without edits. Secrets are only ever read
from the environment (see .env.example); nothing here holds a credential.
"""
from __future__ import annotations

import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = Path(os.environ.get("GDSE_DATASET_DIR", REPO_ROOT / "dataset"))
OUTPUT_PATH = Path(os.environ.get("GDSE_OUTPUT_PATH", REPO_ROOT / "output.csv"))
CACHE_DIR = Path(os.environ.get("GDSE_CACHE_DIR", CODE_DIR / ".cache"))
TRACE_DIR = Path(os.environ.get("GDSE_TRACE_DIR", CODE_DIR / "traces"))
PROMPT_DIR = CODE_DIR / "agent" / "prompts"

# --- LLM provider: Groq through its OpenAI-compatible endpoint -------------
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_API_KEY_ENV = "GROQ_API_KEY"
INTENT_MODEL = os.environ.get("GDSE_INTENT_MODEL", "openai/gpt-oss-20b")
INVESTIGATOR_MODEL = os.environ.get("GDSE_INVESTIGATOR_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = os.environ.get("GDSE_FALLBACK_MODEL", "qwen/qwen3.8-27b")

INTENT_BATCH_SIZE = int(os.environ.get("GDSE_INTENT_BATCH_SIZE", "25"))
INVESTIGATOR_MAX_ITERATIONS = int(os.environ.get("GDSE_INVESTIGATOR_MAX_ITERATIONS", "6"))
MAX_OUTPUT_TOKENS = int(os.environ.get("GDSE_MAX_OUTPUT_TOKENS", "1200"))
TOOL_RESULT_CHAR_LIMIT = int(os.environ.get("GDSE_TOOL_RESULT_CHAR_LIMIT", "1500"))
MAX_RETRIES = int(os.environ.get("GDSE_MAX_RETRIES", "3"))

# Free-tier defaults; the client tightens them from x-ratelimit-* headers.
REQUESTS_PER_MINUTE = int(os.environ.get("GDSE_RPM", "30"))
TOKENS_PER_MINUTE = int(os.environ.get("GDSE_TPM", "8000"))

# Published Groq on-demand prices (USD per 1M tokens), used only to project
# what the free-tier run would cost on the paid tier. Looked up 2026-10-24.
PRICE_PER_MTOK = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "qwen/qwen3.8-27b": (0.80, 4.00),
}


def groq_api_key() -> str | None:
    """Return the Groq key from the environment, or None for deterministic mode."""
    value = os.environ.get(GROQ_API_KEY_ENV, "").strip()
    return value or None
