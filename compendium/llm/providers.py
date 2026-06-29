"""Pure provider helpers — URL/header/payload shaping for OpenAI-compatible and
Anthropic endpoints. Adapted from Odysseus `llm_core` / `endpoint_resolver`, with the
DB/owner/Tailscale coupling stripped (Compendium is single-user/local).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

# Models whose OpenAI-compatible API wants `max_completion_tokens` instead of
# `max_tokens` (OpenAI o-series / gpt-5 reasoning models).
_MAX_COMPLETION_TOKENS_MODELS = (
    "o1", "o3", "o4", "gpt-5",
)


def detect_provider(url: str) -> str:
    """Detect the API family from a base URL."""
    u = (url or "").lower()
    if "anthropic.com" in u:
        return "anthropic"
    if "openrouter.ai" in u:
        return "openrouter"
    if "groq.com" in u:
        return "groq"
    return "openai"


def uses_max_completion_tokens(model: str) -> bool:
    """True if the model's OpenAI-compatible API wants max_completion_tokens."""
    if not model:
        return False
    m = model.lower()
    return any(m.startswith(p) or f"/{p}" in m for p in _MAX_COMPLETION_TOKENS_MODELS)


def normalize_base(url: str) -> str:
    """Strip known API path suffixes from a base URL → the bare base."""
    url = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/v1/messages", "/messages", "/models"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def anthropic_api_root(base: str) -> str:
    """Anthropic's API root, preserving /v1 only where it's not Anthropic's own host."""
    base = (base or "").strip().rstrip("/")
    host = urlparse(base).hostname or ""
    if host.endswith("anthropic.com") and base.endswith("/v1"):
        return base[:-3].rstrip("/")
    return base


def normalize_anthropic_url(url: str) -> str:
    """Ensure an Anthropic base points at /v1/messages."""
    url = (url or "").rstrip("/")
    if url.endswith("/v1/messages"):
        return url
    if url.endswith("/v1"):
        return url + "/messages"
    return anthropic_api_root(url) + "/v1/messages"


def build_chat_url(base: str) -> str:
    """The chat-completions URL for a base. Anthropic → /v1/messages, else
    OpenAI-compatible → /chat/completions. `base` may already include /v1."""
    base = normalize_base(base)
    provider = detect_provider(base)
    if provider == "anthropic":
        return normalize_anthropic_url(base)
    # OpenAI-compatible servers expect the /v1 prefix on the base (most users
    # paste ".../v1"); don't strip it here — normalize_base already removed only
    # the call-path suffixes.
    return base + "/chat/completions"


def models_url(base: str) -> str:
    """The model-listing URL for a base (OpenAI-compatible /models)."""
    base = normalize_base(base)
    if detect_provider(base) == "anthropic":
        return anthropic_api_root(base) + "/v1/models"
    return base + "/models"


def build_headers(api_key: Optional[str], base: str) -> dict:
    """Auth + content headers for an endpoint."""
    provider = detect_provider(base)
    headers: dict = {"Content-Type": "application/json"}
    if provider == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers.setdefault("HTTP-Referer", "https://github.com/anthropics/claude-code")
        headers.setdefault("X-Title", "Dwell / Compendium")
    return headers
