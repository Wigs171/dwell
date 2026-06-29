"""API cost tracking and budget enforcement.

Models the three-tier Anthropic pricing: base input/output, cached-read
(90% off), and cache-write (1.25x for 5-min TTL, 2x for 1-hour TTL).
Batch-API calls are a flat 50% multiplier on the final cost.

Prompt caching stacks with the Batch discount (both multipliers apply),
so the max possible savings on a repeated system prompt + tool block is
~0.5 * 0.1 = ~95% off the base input rate.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

from compendium.models import GuardrailConfig

# Anthropic list prices per 1M tokens (USD) — verified April 2026.
# Opus 4.7 shares 4.6 pricing but its tokenizer produces up to 35% more
# tokens per char, so the effective $/text cost is higher on 4.7.
MODEL_COSTS: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    # Local models via Ollama / vLLM — electricity + VRAM, no per-token
    # API billing. Recorded at $0 so per-model telemetry still counts
    # calls + tokens (useful for "why was this ingest cheap?" postmortems).
    "gemma4:e4b": {"input": 0.0, "output": 0.0},
    "gemma4:e2b": {"input": 0.0, "output": 0.0},
    "gemma4:26b-a4b": {"input": 0.0, "output": 0.0},
    "gemma4:31b": {"input": 0.0, "output": 0.0},
}
DEFAULT_COST = {"input": 3.0, "output": 15.0}
_ZERO_COST = {"input": 0.0, "output": 0.0}
# Prefixes that identify locally-served models (Ollama / vLLM). Matching
# here prevents a slightly-off tag (`gemma3:4b`, `llama3.3:70b-instruct`,
# `qwen3:32b`) from being silently billed at the $3/$15 default when the
# actual inference is free.
_LOCAL_MODEL_PREFIXES = ("gemma", "llama", "qwen", "mistral", "phi", "deepseek-r1-local")


def _resolve_model_costs(model: str) -> dict[str, float]:
    if model in MODEL_COSTS:
        return MODEL_COSTS[model]
    lowered = model.lower()
    if any(lowered.startswith(p) for p in _LOCAL_MODEL_PREFIXES):
        return _ZERO_COST
    return DEFAULT_COST

# Prompt-cache multipliers applied to the base input rate.
CACHE_READ_MULTIPLIER = 0.1         # 90% off on a cache hit
CACHE_WRITE_5M_MULTIPLIER = 1.25    # breaks even after 1 re-use
CACHE_WRITE_1H_MULTIPLIER = 2.0     # breaks even after ~2 re-uses

# Message Batches API: flat 50% off input + output, stacks with caching.
BATCH_MULTIPLIER = 0.5


class BudgetExceeded(Exception):
    """Raised when cost or call-count limits are hit."""

    def __init__(self, reason: str, summary: dict):
        self.reason = reason
        self.summary = summary
        super().__init__(f"Budget exceeded: {reason}")


@dataclass
class CostTracker:
    """
    Thread-safe tracker for cumulative API costs.
    Uses asyncio.Lock for safe use with parallel entry generation.
    """

    guardrails: GuardrailConfig
    _total_input_tokens: int = field(default=0, init=False)
    _total_output_tokens: int = field(default=0, init=False)
    _total_sub_calls: int = field(default=0, init=False)
    _total_root_calls: int = field(default=0, init=False)
    _estimated_cost: float = field(default=0.0, init=False)
    _per_model_costs: dict = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _sync_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def _estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_ttl: str = "1h",
        is_batch: bool = False,
    ) -> float:
        """Cost estimate that accounts for prompt-cache + batch pricing.

        `input_tokens` is the Anthropic "billable uncached input" count —
        i.e. fresh tokens that were neither a cache hit nor a cache write.
        `cache_read_tokens` and `cache_write_tokens` are billed at the
        discounted / premium rates respectively.
        """
        costs = _resolve_model_costs(model)
        in_rate = costs["input"] / 1_000_000
        out_rate = costs["output"] / 1_000_000
        write_mul = (
            CACHE_WRITE_1H_MULTIPLIER if cache_write_ttl == "1h"
            else CACHE_WRITE_5M_MULTIPLIER
        )
        cost = (
            input_tokens * in_rate
            + cache_read_tokens * in_rate * CACHE_READ_MULTIPLIER
            + cache_write_tokens * in_rate * write_mul
            + output_tokens * out_rate
        )
        if is_batch:
            cost *= BATCH_MULTIPLIER
        return cost

    async def record_call_async(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        is_sub_call: bool = False,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_ttl: str = "1h",
        is_batch: bool = False,
    ) -> None:
        async with self._lock:
            self._record(
                input_tokens, output_tokens, model, is_sub_call,
                cache_read_tokens, cache_write_tokens, cache_write_ttl, is_batch,
            )

    def record_call(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        is_sub_call: bool = False,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_ttl: str = "1h",
        is_batch: bool = False,
    ) -> None:
        # threading.Lock guards concurrent writes when the sync client is
        # driven from a ThreadPoolExecutor (e.g. parallel figure
        # transcription). Uncontended under normal sync use.
        with self._sync_lock:
            self._record(
                input_tokens, output_tokens, model, is_sub_call,
                cache_read_tokens, cache_write_tokens, cache_write_ttl, is_batch,
            )

    def _record(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        is_sub_call: bool,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_ttl: str = "1h",
        is_batch: bool = False,
    ) -> None:
        # Track total input as the sum of all three billable categories so
        # token-count telemetry matches Anthropic's aggregate usage counts.
        total_input = input_tokens + cache_read_tokens + cache_write_tokens
        self._total_input_tokens += total_input
        self._total_output_tokens += output_tokens
        cost = self._estimate_cost(
            input_tokens, output_tokens, model,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_write_ttl=cache_write_ttl,
            is_batch=is_batch,
        )
        self._estimated_cost += cost
        if is_sub_call:
            self._total_sub_calls += 1
        else:
            self._total_root_calls += 1
        # Per-model accumulation. cache_read/write tokens are tracked
        # separately so `costs` reports show how much caching is actually
        # helping — operationally useful for tuning TTLs.
        if model not in self._per_model_costs:
            self._per_model_costs[model] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "calls": 0,
                "cost": 0.0,
            }
        m = self._per_model_costs[model]
        m["input_tokens"] += total_input
        m["output_tokens"] += output_tokens
        m["cache_read_tokens"] += cache_read_tokens
        m["cache_write_tokens"] += cache_write_tokens
        m["calls"] += 1
        m["cost"] += cost

    def check_budget(self) -> None:
        summary = self.get_summary()
        if self._estimated_cost > self.guardrails.max_cost_dollars:
            raise BudgetExceeded(
                f"Estimated cost ${self._estimated_cost:.2f} exceeds "
                f"budget ${self.guardrails.max_cost_dollars:.2f}",
                summary,
            )
        if self._total_sub_calls > self.guardrails.max_total_sub_calls:
            raise BudgetExceeded(
                f"Total sub-calls {self._total_sub_calls} exceeds "
                f"limit {self.guardrails.max_total_sub_calls}",
                summary,
            )

    def get_summary(self) -> dict:
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_sub_calls": self._total_sub_calls,
            "total_root_calls": self._total_root_calls,
            "estimated_cost_usd": round(self._estimated_cost, 4),
            "per_model": {
                k: {**v, "cost": round(v["cost"], 4)}
                for k, v in self._per_model_costs.items()
            },
        }
