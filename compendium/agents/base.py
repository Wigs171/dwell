"""
Base agent class implementing the RLM REPL interaction loop (Algorithm 1).

Every agent (scope, entry, quality) inherits from this and provides:
- A system prompt defining its role and available functions
- Initial context variables for the REPL
"""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from compendium.config import CompendiumConfig
from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import GuardrailConfig
from compendium.repl.environment import REPLEnvironment
from compendium.repl.functions import (
    make_deep_search_fn,
    make_fetch_url_fn,
    make_llm_query_fn,
    make_llm_query_many_fn,
    make_partition_fn,
    make_remaining_budget_fn,
    make_rlm_query_fn,
    make_view_image_fn,
    make_web_search_fn,
)


class MaxIterationsExceeded(Exception):
    def __init__(self, iterations: int, last_metadata: Any = None):
        self.iterations = iterations
        self.last_metadata = last_metadata
        super().__init__(f"Max REPL iterations ({iterations}) exceeded")


class BaseAgent:
    """
    Base class for all RLM-style agents.
    Encapsulates the REPL interaction loop from Algorithm 1.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        config: CompendiumConfig,
        cost_tracker: CostTracker,
        model_override: str | None = None,
        sub_call_model_override: str | None = None,
        depth: int = 0,
    ):
        self.client = client
        self.config = config
        self.cost_tracker = cost_tracker
        self.guardrails = config.get_guardrails()
        self._model_override = model_override
        self._sub_call_model_override = sub_call_model_override
        # Recursion depth for nested RLMs (root = 0). `rlm_query()` spawns
        # a child at depth+1 and refuses to recurse past MAX_RLM_DEPTH.
        self._depth = depth

    @property
    def effective_model(self) -> str:
        """The model this agent uses for its REPL loop."""
        return self._model_override or self.config.model_root

    @property
    def effective_sub_call_model(self) -> str:
        """The model this agent uses for llm_query sub-calls."""
        return self._sub_call_model_override or self.config.model_sub_call

    def get_system_prompt(self) -> str:
        """Override in subclasses. Returns the system prompt for this agent role."""
        raise NotImplementedError

    def _create_repl(self) -> REPLEnvironment:
        """Create a fresh REPL environment with standard functions registered."""
        repl = REPLEnvironment(self.guardrails)
        return repl

    def _register_standard_functions(self, repl: REPLEnvironment) -> None:
        """Register the standard REPL functions.

        Standard set (RLM scaffold):
        - llm_query(prompt)               flat sub-call
        - llm_query_many(prompts)         parallel fan-out (paper's parallel pattern)
        - rlm_query(prompt, ...)          recursive child RLM (paper's nested sub-LM)
        - partition(text, n)              map-reduce primitive (chunking)
        - remaining_budget()              budget introspection for fan-out decisions
        - web_search, fetch_url, deep_search, view_image
        """
        llm_query = make_llm_query_fn(
            client=self.client,
            model=self.effective_sub_call_model,
            cost_tracker=self.cost_tracker,
        )
        repl.register_function("llm_query", llm_query)
        repl.register_function(
            "llm_query_many",
            make_llm_query_many_fn(
                client=self.client,
                model=self.effective_sub_call_model,
                cost_tracker=self.cost_tracker,
            ),
        )
        repl.register_function(
            "rlm_query",
            make_rlm_query_fn(
                client=self.client,
                model=self.effective_sub_call_model,
                cost_tracker=self.cost_tracker,
                guardrails=self.guardrails,
                parent_depth=self._depth,
            ),
        )
        repl.register_function(
            "partition",
            make_partition_fn(),
        )
        repl.register_function(
            "remaining_budget",
            make_remaining_budget_fn(self.cost_tracker),
        )
        # Cell-indexed history controls. Agent-facing wrappers around the
        # REPL's internal pin/evict state — the agent references past
        # cells by the In[N]/Out[N] numbers shown in its message history.
        repl.register_function("pin_cell", repl.pin_cell)
        repl.register_function("evict_cell", repl.evict_cell)
        search_fn = make_web_search_fn(
            provider=self.config.search_provider,
            api_key=self.config.search_api_key,
            jina_api_key=getattr(self.config, "jina_api_key", ""),
        )
        fetch_fn = make_fetch_url_fn(
            jina_api_key=self.config.jina_api_key,
        )
        repl.register_function("web_search", search_fn)
        repl.register_function("fetch_url", fetch_fn)
        repl.register_function(
            "deep_search",
            make_deep_search_fn(search_fn, fetch_fn),
        )
        from compendium.sources.vision_provider import make_vision_provider
        vision_provider = make_vision_provider(
            config=self.config,
            client=self.client,
            cost_tracker=self.cost_tracker,
            model_override=self.effective_sub_call_model
                if (getattr(self.config, "vision_provider", "anthropic") or "anthropic").lower()
                    == "anthropic"
                else None,
        )
        repl.register_function(
            "view_image",
            make_view_image_fn(provider=vision_provider),
        )

    def run(
        self,
        context: dict[str, Any],
        max_iterations_override: int | None = None,
    ) -> Any:
        """
        Run the full REPL loop (Algorithm 1 from the RLM paper).

        Args:
            context: Variables to initialize the REPL with.
            max_iterations_override: If set, overrides the guardrail
                max_repl_iterations for this run (adaptive iterations).

        1. Create REPL and initialize with context variables
        2. Register standard functions
        3. Send metadata-only description of state to LLM
        4. Loop:
           a. LLM generates code
           b. Execute code in REPL
           c. Check for termination (FINAL/FINAL_VAR)
           d. Append metadata to history
           e. Check guardrails
        5. Return final result
        """
        repl = self._create_repl()
        self._register_standard_functions(repl)
        repl.init_state(context)

        # Build initial message with context metadata (not raw content)
        context_meta = repl.get_context_metadata()
        available_fns = repl.get_available_functions()

        initial_user_msg = (
            "REPL initialized. Notebook-style cell indexing is in use — "
            "your cells appear as `In[N]/Out[N]`. Call `pin_cell(n)` to "
            "keep a cell visible after it would otherwise be auto-evicted, "
            "and `evict_cell(n)` to drop one you no longer need.\n\n"
            "Context variables (metadata only):\n"
            f"```json\n{json.dumps(context_meta, indent=2, default=str)}\n```\n\n"
            f"Available functions: {available_fns}\n\n"
            "Write Python code to accomplish your task. "
            "Use print() to inspect variables (stdout will be truncated in history). "
            "Call FINAL('result') or FINAL_VAR('variable_name') when done."
        )

        effective_max = max_iterations_override or self.guardrails.max_repl_iterations
        # Window of visible (non-evicted) cells before auto-eviction
        # kicks in. Chosen so a 50-iter Explorer run won't silently
        # bloat the context beyond ~20 turns of recent history (the
        # older ones collapse to `[evicted]` placeholders).
        max_turns_visible = max(12, min(30, effective_max // 2))

        for iteration in range(effective_max):
            # Rebuild messages from REPL history each turn so that
            # pins/evictions made via pin_cell/evict_cell take effect
            # on the very next generation.
            messages: list[dict[str, str]] = [
                {"role": "user", "content": initial_user_msg}
            ]
            messages.extend(repl.format_history_for_llm())

            # Scheduler nudge: attach a convergence reminder to the
            # tail user message in-flight. Doesn't mutate REPL history —
            # it's just advice for this one generation.
            used = iteration + 1
            remaining = effective_max - used
            pct_used = used / effective_max
            nudge = None
            if pct_used >= 0.9:
                nudge = (
                    f"[scheduler] FINAL WARNING: {remaining} iteration(s) "
                    f"left of {effective_max}. FINAL_VAR your result in the "
                    "next cell."
                )
            elif pct_used >= 0.75:
                nudge = (
                    f"[scheduler] URGENT: {remaining} iterations left. Stop "
                    "gathering, assemble your result and FINAL_VAR it."
                )
            elif pct_used >= 0.5:
                nudge = (
                    f"[scheduler] {used}/{effective_max} iterations used. "
                    "Start assembling your final result."
                )
            if nudge:
                if messages and messages[-1]["role"] == "user":
                    messages[-1] = {
                        "role": "user",
                        "content": messages[-1]["content"] + "\n" + nudge,
                    }
                else:
                    messages.append({"role": "user", "content": nudge})

            # Prompt caching: the system prompt and the initial_user_msg
            # are both stable across every iteration of a single run(),
            # and a typical agent runs for 5–50 turns before FINAL. We
            # place two cache breakpoints with 1h TTL so the second turn
            # onward reads at the 0.1x cached-input rate. The initial
            # user-message block re-uses the same cached content as long
            # as its `text` is byte-identical — which it is, because the
            # scheduler nudge is appended to the *tail* message, not the
            # first one. See cost_tracker.CACHE_* constants for the math.
            system_blocks = [
                {
                    "type": "text",
                    "text": self.get_system_prompt(),
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ]
            cached_messages = _apply_initial_message_cache(messages)

            response = self.client.messages.create(
                model=self.effective_model,
                system=system_blocks,
                messages=cached_messages,
                max_tokens=4096,
            )
            usage = response.usage
            self.cost_tracker.record_call(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                model=self.effective_model,
                is_sub_call=False,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cache_write_ttl="1h",
            )

            full_response = response.content[0].text
            code = self._extract_code(full_response)

            if not code:
                # No code — record a no-op turn so the next rebuild shows
                # "please provide code" in the Out slot, without diverging
                # from the In[N]/Out[N] convention.
                repl.execute(
                    "# [no code in response — please emit a ```python block]\n"
                    "pass"
                )
                continue

            _namespace, _stdout = repl.execute(code)

            if repl.is_terminated:
                return repl.terminal_value

            # Auto-evict oldest unpinned cells once the visible-cell
            # window overflows. Pinned cells survive.
            repl.auto_evict(max_turns_visible)

            self.cost_tracker.check_budget()

        raise MaxIterationsExceeded(
            effective_max,
            repl.get_history()[-1] if repl.get_history() else None,
        )

    @staticmethod
    def _extract_code(response_text: str) -> str:
        """
        Extract Python code from the LLM response.
        Looks for ```python ... ``` blocks first, then bare ``` blocks.
        """
        # Try python-tagged code blocks
        pattern = r"```python\s*\n(.*?)```"
        matches = re.findall(pattern, response_text, re.DOTALL)
        if matches:
            return "\n".join(matches)

        # Try untagged code blocks
        pattern = r"```\s*\n(.*?)```"
        matches = re.findall(pattern, response_text, re.DOTALL)
        if matches:
            return "\n".join(matches)

        # If the entire response looks like code (no markdown), use it directly
        lines = response_text.strip().split("\n")
        if lines and all(
            not line.startswith("#") or line.startswith("# ") for line in lines[:3]
        ):
            # Check if it has Python-like syntax
            code_indicators = ["=", "(", "def ", "for ", "if ", "import ", "print("]
            if any(ind in response_text for ind in code_indicators):
                return response_text.strip()

        return ""


def _apply_initial_message_cache(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rewrite the first user message into cache-enabled content blocks.

    The initial_user_msg is identical on every REPL iteration, so a
    1-hour ephemeral breakpoint there lets the second and later turns
    read it at the 0.1x cached rate. We only rewrite when the first
    message is a plain string; anything more structured (already
    list-of-blocks) is left alone so callers retain full control.
    """
    if not messages:
        return messages
    first = messages[0]
    content = first.get("content")
    if not isinstance(content, str):
        return messages
    rewritten = dict(first)
    rewritten["content"] = [
        {
            "type": "text",
            "text": content,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    return [rewritten] + list(messages[1:])
