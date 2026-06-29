"""AnthropicCompatClient — a drop-in for the Anthropic SDK's `client.messages.create()`
that dispatches to an OpenAI-compatible `/chat/completions` endpoint and returns
Anthropic-shaped response objects, so Compendium's agents run unchanged on ANY provider.

Translation (Anthropic call → OpenAI request → Anthropic-shaped response) follows the
Odysseus `llm_core` adapter logic in reverse.

Known limits (handled by callers, not here): the Anthropic Batches API
(`messages.batches`) and async `await ...messages.create` are NOT provided — callers
detect a non-Anthropic client and fall back to sequential sync calls.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from compendium.llm.providers import build_chat_url, build_headers, uses_max_completion_tokens


# ---- Anthropic-shaped response objects -------------------------------------
class _TextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict):  # noqa: A002 — mirror Anthropic
        self.id = id
        self.name = name
        self.input = input


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _Response:
    def __init__(self, content: list, usage: _Usage, stop_reason: str, model: str):
        self.content = content
        self.usage = usage
        self.stop_reason = stop_reason
        self.model = model
        self.role = "assistant"


# ---- request translation ----------------------------------------------------
def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and block.get("type") == "text":
        return block.get("text", "")
    return ""


def _system_to_text(system: Any) -> str:
    """Anthropic `system` (str OR list of {type:text,text,cache_control}) → plain text."""
    if not system:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n\n".join(_block_text(b) for b in system if _block_text(b))
    return str(system)


def _content_to_openai(content: Any) -> Any:
    """Anthropic message `content` → OpenAI content. Plain text stays a string;
    mixed/blocked content becomes OpenAI content parts (text + image_url)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list = []
    texts: list = []
    for b in content:
        if not isinstance(b, dict):
            texts.append(str(b))
            continue
        t = b.get("type")
        if t == "text":
            txt = b.get("text", "")
            texts.append(txt)
            parts.append({"type": "text", "text": txt})
        elif t == "image":
            src = b.get("source") or {}
            if src.get("type") == "base64":
                parts.append({"type": "image_url", "image_url": {
                    "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"}})
            elif src.get("type") == "url":
                parts.append({"type": "image_url", "image_url": {"url": src.get("url", "")}})
    # text-only → a plain string (widest compatibility); otherwise structured parts
    if parts and all(p.get("type") == "text" for p in parts):
        return "\n".join(texts)
    return parts or "\n".join(texts)


def _messages_to_openai(messages: list) -> list:
    """Anthropic messages → OpenAI messages, expanding tool_use / tool_result turns."""
    out: list = []
    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, list):
            tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            if tool_results:                         # Anthropic packs results in a user turn
                for tr in tool_results:
                    rc = tr.get("content")
                    out.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": rc if isinstance(rc, str) else _system_to_text(rc),
                    })
                continue
            if tool_uses and role == "assistant":
                msg: dict = {"role": "assistant", "content": _system_to_text(content) or None,
                             "tool_calls": [{
                                 "id": tu.get("id", ""), "type": "function",
                                 "function": {"name": tu.get("name", ""),
                                              "arguments": json.dumps(tu.get("input", {}))},
                             } for tu in tool_uses]}
                out.append(msg)
                continue
        out.append({"role": role, "content": _content_to_openai(content)})
    return out


def _tools_to_openai(tools: Optional[list]) -> Optional[list]:
    if not tools:
        return None
    out = []
    for t in tools:
        # Anthropic tool: {name, description, input_schema}
        out.append({"type": "function", "function": {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out


def _tool_choice_to_openai(tc: Any) -> Any:
    if not tc:
        return None
    if isinstance(tc, dict):
        kind = tc.get("type")
        if kind == "tool" and tc.get("name"):
            return {"type": "function", "function": {"name": tc["name"]}}
        if kind == "any":
            return "required"
        if kind == "auto":
            return "auto"
    return None


_STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
             "content_filter": "end_turn", "function_call": "tool_use"}


class _Messages:
    def __init__(self, base_url: str, api_key: Optional[str], timeout: float):
        self._url = build_chat_url(base_url)
        self._headers = build_headers(api_key, base_url)
        self._timeout = timeout

    def create(self, *, model: str, messages: list, max_tokens: int = 4096,
               system: Any = None, tools: Optional[list] = None, tool_choice: Any = None,
               temperature: float = 1.0, **_ignored) -> _Response:
        oa_messages: list = []
        sys_text = _system_to_text(system)
        if sys_text:
            oa_messages.append({"role": "system", "content": sys_text})
        oa_messages.extend(_messages_to_openai(messages))

        tok_key = "max_completion_tokens" if uses_max_completion_tokens(model) else "max_tokens"
        payload: dict = {"model": model, "messages": oa_messages, tok_key: max_tokens,
                         "temperature": temperature}
        oa_tools = _tools_to_openai(tools)
        if oa_tools:
            payload["tools"] = oa_tools
            oa_choice = _tool_choice_to_openai(tool_choice)
            if oa_choice is not None:
                payload["tool_choice"] = oa_choice

        r = httpx.post(self._url, headers=self._headers, json=payload, timeout=self._timeout)
        if not r.is_success:
            detail = r.text[:300]
            raise RuntimeError(f"LLM endpoint {r.status_code}: {detail}")
        data = r.json()

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        blocks: list = []
        text = msg.get("content")
        if isinstance(text, list):                   # some servers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if text:
            blocks.append(_TextBlock(text))
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            blocks.append(_ToolUseBlock(tc.get("id", ""), fn.get("name", ""), args))
        if not blocks:
            blocks.append(_TextBlock(""))

        u = data.get("usage") or {}
        usage = _Usage(int(u.get("prompt_tokens", 0) or 0), int(u.get("completion_tokens", 0) or 0))
        stop_reason = _STOP_MAP.get(choice.get("finish_reason"), "end_turn")
        return _Response(blocks, usage, stop_reason, data.get("model", model))


class AnthropicCompatClient:
    """Quacks like `anthropic.Anthropic()` for `.messages.create(...)`, but talks to an
    OpenAI-compatible endpoint. `is_compat = True` lets callers (e.g. the batch path)
    detect it and fall back to sequential calls instead of the Anthropic Batches API."""

    is_compat = True

    def __init__(self, base_url: str, api_key: Optional[str], timeout: float = 600.0):
        self.base_url = base_url
        self.messages = _Messages(base_url, api_key, timeout)
