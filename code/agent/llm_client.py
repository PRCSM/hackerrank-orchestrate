"""Groq chat client (OpenAI-compatible API) with free-tier rate limiting.

- Token bucket per model for requests/minute and tokens/minute. Limits start
  from config defaults and are tightened from the `x-ratelimit-*` headers of
  every response, so the client never sends faster than the account allows.
- 429 / 5xx: honour `retry-after` (or back off exponentially), at most
  MAX_RETRIES times. A 429 that names a daily quota raises QuotaExhausted so
  the caller can switch to the fallback model, then to deterministic mode.
- Every call's `usage` is recorded (storage/usage.py) for usage_report.md.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from config import GROQ_BASE_URL, MAX_RETRIES, REQUESTS_PER_MINUTE, TOKENS_PER_MINUTE, groq_api_key
from storage.usage import UsageRecorder

DAILY_QUOTA = re.compile(r"per day|tokens per day|requests per day|\bTPD\b|\bRPD\b", re.IGNORECASE)


class QuotaExhausted(RuntimeError):
    """The model's daily quota is used up; do not retry today."""


class LLMUnavailable(RuntimeError):
    """Retries exhausted or the API is unreachable."""


@dataclass
class _Bucket:
    requests_per_minute: int
    tokens_per_minute: int
    sent: list[tuple[float, int]] = field(default_factory=list)  # (time, estimated tokens)

    def wait_time(self, tokens: int, now: float) -> float:
        self.sent = [(t, n) for t, n in self.sent if now - t < 60.0]
        if len(self.sent) < self.requests_per_minute and sum(n for _, n in self.sent) + tokens <= self.tokens_per_minute:
            return 0.0
        if not self.sent:
            return 0.0
        return max(0.0, 60.0 - (now - self.sent[0][0])) + 0.05

    def record(self, tokens: int, now: float) -> None:
        self.sent.append((now, tokens))


def estimate_tokens(messages: list[dict[str, Any]], tools: list[dict] | None, max_tokens: int) -> int:
    text = json.dumps(messages, ensure_ascii=False) + (json.dumps(tools) if tools else "")
    return len(text) // 4 + max_tokens


@dataclass
class ChatResult:
    message: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int


class GroqClient:
    def __init__(self, usage: UsageRecorder, *, transport: Any = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        if transport is None:
            from openai import OpenAI

            transport = OpenAI(base_url=GROQ_BASE_URL, api_key=groq_api_key(), max_retries=0)
        self.transport = transport
        self.usage = usage
        self.sleep = sleep
        self.clock = clock
        self.buckets: dict[str, _Bucket] = {}

    def _bucket(self, model: str) -> _Bucket:
        return self.buckets.setdefault(model, _Bucket(REQUESTS_PER_MINUTE, TOKENS_PER_MINUTE))

    def _apply_headers(self, model: str, headers: Any) -> None:
        bucket = self._bucket(model)
        for header, attr in (("x-ratelimit-limit-requests", "requests_per_minute"), ("x-ratelimit-limit-tokens", "tokens_per_minute")):
            value = headers.get(header) if headers else None
            if value and str(value).isdigit():
                setattr(bucket, attr, min(getattr(bucket, attr), int(value)))

    def chat(self, model: str, messages: list[dict[str, Any]], *, purpose: str, case_id: str = "",
             tools: list[dict] | None = None, json_mode: bool = False, max_tokens: int = 800) -> ChatResult:
        estimate = estimate_tokens(messages, tools, max_tokens)
        bucket = self._bucket(model)
        for attempt in range(MAX_RETRIES + 1):
            wait = bucket.wait_time(estimate, self.clock())
            if wait:
                self.sleep(wait)
            bucket.record(estimate, self.clock())
            kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                raw = self.transport.chat.completions.with_raw_response.create(**kwargs)
            except Exception as error:  # SDK errors carry status_code / response
                status = getattr(error, "status_code", None)
                message = str(error)
                if status == 429 and DAILY_QUOTA.search(message):
                    raise QuotaExhausted(f"{model}: {message[:200]}") from error
                if status in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                    response = getattr(error, "response", None)
                    retry_after = response.headers.get("retry-after") if response is not None else None
                    self.sleep(float(retry_after) if retry_after else 2.0 ** (attempt + 1))
                    continue
                raise LLMUnavailable(f"{model}: {message[:200]}") from error
            self._apply_headers(model, getattr(raw, "headers", None))
            completion = raw.parse()
            usage = completion.usage
            result = ChatResult(
                message=completion.choices[0].message.model_dump(exclude_none=True),
                model=model,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )
            self.usage.record(model=model, purpose=purpose, case_id=case_id,
                              input_tokens=result.input_tokens, output_tokens=result.output_tokens)
            return result
        raise LLMUnavailable(f"{model}: retries exhausted")
