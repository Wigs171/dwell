"""Provider-agnostic LLM layer for Compendium.

Lets the ingest pipeline run against ANY provider (OpenAI-compatible or Anthropic),
not just the Anthropic SDK. Adapted from the Odysseus project's `llm_core` /
`endpoint_resolver` (same author): an OpenAI-compatible default with an Anthropic
adapter. The key piece is `anthropic_compat.AnthropicCompatClient`, a drop-in for the
Anthropic SDK's `client.messages.create(...)` that dispatches to an OpenAI-compatible
`/chat/completions` endpoint and returns Anthropic-shaped response objects — so the
existing agents work unchanged.
"""
from compendium.llm.providers import (
    detect_provider,
    normalize_base,
    build_chat_url,
    build_headers,
    uses_max_completion_tokens,
)

__all__ = [
    "detect_provider",
    "normalize_base",
    "build_chat_url",
    "build_headers",
    "uses_max_completion_tokens",
]
