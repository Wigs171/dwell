"""
Minimal RLM child agent spawned by rlm_query().

This is deliberately NOT a subclass of BaseAgent — BaseAgent imports
the REPL factories that construct `rlm_query`, so inheriting would
create a circular import. Instead, RLMChild reimplements a stripped-
down version of the Algorithm 1 REPL loop for recursive delegation.

A child receives:
- the parent's task prompt
- an optional context dict (becomes REPL variables)
- a depth stamp and max-iterations cap
- the same sub-call model and cost tracker as its parent

The child's REPL has llm_query, llm_query_many, partition,
remaining_budget, and its own depth-capped rlm_query registered —
but NOT web_search/fetch_url/view_image by default, to keep scope
narrow and costs predictable. Parents that need external tools
should do the external calls themselves and pass raw content via
the `context` dict.

Terminates on FINAL() / FINAL_VAR() or when max_iterations is hit.
"""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import GuardrailConfig


DEFAULT_CHILD_SYSTEM = """\
You are a child RLM spawned by a parent agent to handle a delegated
sub-task. You operate inside a persistent Python REPL.

## Your environment

Variables visible:
- `task_prompt` (str) — the request from your parent
- any additional variables your parent passed in `context`

Functions:
- `llm_query(prompt)` — flat sub-LLM call
- `llm_query_many(prompts)` — parallel fan-out; returns list in order
- `rlm_query(prompt, context=..., max_iterations=...)` — recurse further
  (depth-capped; returns `[RLM DEPTH CAP]` when the limit is hit)
- `partition(text, chunk_size=40000, overlap=0)` — split long text
- `remaining_budget()` — returns {spent_usd, remaining_usd, sub_calls_remaining, ...}
- `FINAL(value)` / `FINAL_VAR('name')` — terminate with a result

## Rules

- Answer the parent's request directly and concisely.
- If the task is long-context: partition + llm_query_many (map-reduce).
- Before fanning out, check `remaining_budget()`; skip parallel fan-out
  if remaining_usd is tight.
- Terminate ASAP with `FINAL_VAR('answer')` where `answer` is the string
  your parent should receive.
- Do NOT explain what you're doing outside code cells — the parent only
  sees your final string.

Reply with a single ```python``` block per turn.
"""


class _ChildTermination(Exception):
    def __init__(self, value: Any):
        self.value = value


class RLMChild:
    """Bounded child RLM agent used by rlm_query()."""

    def __init__(
        self,
        *,
        client: anthropic.Anthropic,
        model: str,
        cost_tracker: CostTracker,
        guardrails: GuardrailConfig,
        depth: int,
        max_iterations: int = 10,
        system_prompt: str | None = None,
    ):
        self.client = client
        self.model = model
        self.cost_tracker = cost_tracker
        self.guardrails = guardrails
        self.depth = depth
        # Clamp iterations to something reasonable — children should not
        # be expensive by default. A child that needs 50 iterations
        # probably shouldn't be a child.
        self.max_iterations = max(1, min(max_iterations, 15))
        self.system_prompt = system_prompt or DEFAULT_CHILD_SYSTEM

    def run(self, *, prompt: str, context: dict[str, Any]) -> Any:
        """Run the child REPL loop and return the FINAL value."""
        # Late imports: environment/functions reference each other
        # and we want to avoid module-load cycles.
        from compendium.repl.environment import REPLEnvironment
        from compendium.repl.functions import (
            make_llm_query_fn,
            make_llm_query_many_fn,
            make_partition_fn,
            make_remaining_budget_fn,
            make_rlm_query_fn,
        )

        repl = REPLEnvironment(self.guardrails)

        # Register the narrow child toolkit.
        repl.init_state({"task_prompt": prompt, **context})
        repl.register_function(
            "llm_query",
            make_llm_query_fn(
                client=self.client,
                model=self.model,
                cost_tracker=self.cost_tracker,
            ),
        )
        repl.register_function(
            "llm_query_many",
            make_llm_query_many_fn(
                client=self.client,
                model=self.model,
                cost_tracker=self.cost_tracker,
            ),
        )
        repl.register_function(
            "rlm_query",
            make_rlm_query_fn(
                client=self.client,
                model=self.model,
                cost_tracker=self.cost_tracker,
                guardrails=self.guardrails,
                parent_depth=self.depth,
            ),
        )
        repl.register_function("partition", make_partition_fn())
        repl.register_function(
            "remaining_budget",
            make_remaining_budget_fn(self.cost_tracker),
        )

        # Build the kickoff message — metadata only, the paper's discipline.
        context_meta = repl.get_context_metadata()
        available = repl.get_available_functions()
        initial_user = (
            f"RLM child depth={self.depth} initialized. "
            f"Iteration cap: {self.max_iterations}.\n"
            f"Context variables (metadata only):\n"
            f"```json\n{json.dumps(context_meta, indent=2, default=str)}\n```\n\n"
            f"Available functions: {available}\n\n"
            "Your task is in `task_prompt`. Print it, think briefly in code, "
            "then call FINAL_VAR('answer') with the string to return to the parent."
        )
        messages: list[dict[str, str]] = [{"role": "user", "content": initial_user}]

        for iteration in range(self.max_iterations):
            response = self.client.messages.create(
                model=self.model,
                system=self.system_prompt,
                messages=messages,
                max_tokens=4096,
            )
            self.cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=self.model,
                is_sub_call=True,  # the whole child counts as sub-work
            )
            full_response = response.content[0].text
            code = _extract_code(full_response)

            if not code:
                messages.append({"role": "assistant", "content": full_response})
                messages.append({
                    "role": "user",
                    "content": "Provide Python code in a ```python block.",
                })
                continue

            _, _stdout = repl.execute(code)
            if repl.is_terminated:
                return repl.terminal_value

            turn = repl.get_history()[-1]
            result_parts = [f"[In[{turn.turn_number}] executed]"]
            if turn.error:
                result_parts.append(f"ERROR: {turn.error[:300]}")
            else:
                result_parts.append("OK.")
            if turn.stdout_length > 0:
                result_parts.append(
                    f"Out[{turn.turn_number}] ({turn.stdout_length} chars): "
                    f"{turn.stdout_prefix}"
                )
            if turn.variables_changed:
                result_parts.append(f"New vars: {turn.variables_changed}")

            messages.append({"role": "assistant", "content": f"```python\n{code}\n```"})

            remaining = self.max_iterations - (iteration + 1)
            if remaining <= 1:
                result_parts.append(
                    f"\nFINAL WARNING: {remaining} iteration(s) left. "
                    "Call FINAL_VAR('answer') in your NEXT cell."
                )
            messages.append({"role": "user", "content": "\n".join(result_parts)})
            self.cost_tracker.check_budget()

        # Hit the cap without termination — return whatever `answer` is
        # in the namespace, or a diagnostic string.
        ns_answer = repl.get_variable("answer")
        if ns_answer is not None:
            return ns_answer
        return f"[RLM CHILD UNTERMINATED] depth={self.depth} hit iter cap; no answer var set"


_CODE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _extract_code(text: str) -> str:
    matches = _CODE_RE.findall(text)
    return "\n".join(matches) if matches else ""
