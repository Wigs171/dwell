"""
Persistent Python REPL environment implementing Algorithm 1 from the RLM paper.

Key invariants:
- The user prompt (source material) lives in REPL variables, NOT in the LLM context window
- Each turn appends only truncated metadata to LLM history, never raw content
- Sub-LLM calls are available as callable functions within the REPL
- Termination via FINAL() or FINAL_VAR()
"""

from __future__ import annotations

import io
import sys
import traceback
from typing import Any, Callable, Optional

from compendium.models import GuardrailConfig, REPLTurnMetadata
from compendium.repl.sandbox import build_namespace


class TerminationSignal(Exception):
    """Raised by FINAL/FINAL_VAR to exit the REPL loop."""

    def __init__(self, value: Any):
        self.value = value
        super().__init__("REPL termination")


class REPLEnvironment:
    """
    Persistent Python REPL that maintains state across agent iterations.

    The namespace dictionary persists between execute() calls, allowing
    variables set in one turn to be read in subsequent turns.
    """

    def __init__(self, guardrails: GuardrailConfig):
        self._guardrails = guardrails
        self._namespace: dict[str, Any] = {}
        self._registered_functions: dict[str, Callable] = {}
        self._history: list[REPLTurnMetadata] = []
        self._turn_count = 0
        self._terminated = False
        self._terminal_value: Any = None
        # Cell-indexed history state. Per the RLM paper's Jupyter-style
        # notebook convention (In[N]/Out[N]), the agent can address
        # individual cells by index: pin critical results, evict stale
        # ones. `_pinned_cells` never get auto-evicted; `_evicted_cells`
        # are replaced by a `[In[N] evicted]` placeholder in the
        # formatted history, freeing context budget.
        self._pinned_cells: set[int] = set()
        self._evicted_cells: set[int] = set()

    def init_state(self, context_vars: dict[str, Any]) -> None:
        """
        Initialize REPL state with context variables.
        These become variables in the namespace, NOT context window content.
        """
        # Build termination functions
        def final(value: str) -> None:
            raise TerminationSignal(value)

        def final_var(variable_name: str) -> None:
            if variable_name not in self._namespace:
                raise ValueError(
                    f"FINAL_VAR: variable '{variable_name}' not found in namespace. "
                    f"Available: {[k for k in self._namespace if not k.startswith('_')]}"
                )
            raise TerminationSignal(self._namespace[variable_name])

        self._registered_functions["FINAL"] = final
        self._registered_functions["FINAL_VAR"] = final_var

        self._namespace = build_namespace(
            context_vars=context_vars,
            registered_functions=self._registered_functions,
        )
        self._turn_count = 0
        self._history = []
        self._terminated = False
        self._terminal_value = None

    def register_function(self, name: str, fn: Callable) -> None:
        """Register a function callable from within the REPL."""
        self._registered_functions[name] = fn
        self._namespace[name] = fn

    def execute(self, code: str) -> tuple[dict[str, Any], str]:
        """
        Execute code in the persistent REPL.

        Returns (namespace_snapshot, stdout_string).
        Exceptions are caught and returned as stdout error messages.
        """
        self._turn_count += 1
        stdout_capture = io.StringIO()
        old_stdout = sys.stdout
        error: Optional[str] = None
        variables_before = set(self._namespace.keys())

        try:
            sys.stdout = stdout_capture
            # LLMs frequently emit non-raw strings containing \| (table
            # syntax), \_ (markdown escapes), etc. that trigger harmless
            # SyntaxWarnings during compile. Silence them per-execute.
            import warnings as _warnings
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore", category=SyntaxWarning)
                compiled = compile(code, "<repl>", "exec")
                exec(compiled, self._namespace)
        except TerminationSignal as ts:
            self._terminated = True
            self._terminal_value = ts.value
        except Exception:
            error = traceback.format_exc()
            stdout_capture.write(f"\n[ERROR]\n{error}")
        finally:
            sys.stdout = old_stdout

        stdout = stdout_capture.getvalue()
        variables_after = set(self._namespace.keys())
        changed = list(variables_after - variables_before)
        # Also detect modified variables (simple heuristic: track explicitly named assignments)
        # For now, only new variables are tracked

        prefix_len = self._guardrails.stdout_prefix_length
        metadata = REPLTurnMetadata(
            turn_number=self._turn_count,
            code_executed=code,
            stdout_length=len(stdout),
            stdout_prefix=stdout[:prefix_len] if stdout else "",
            variables_changed=changed,
            error=error,
        )
        self._history.append(metadata)

        return dict(self._namespace), stdout

    @property
    def is_terminated(self) -> bool:
        return self._terminated

    @property
    def terminal_value(self) -> Any:
        return self._terminal_value

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def get_history(self) -> list[REPLTurnMetadata]:
        """Return the full metadata-only history."""
        return list(self._history)

    def get_context_metadata(self) -> dict[str, Any]:
        """
        Return metadata about the initial state for the LLM's first message.
        This replaces putting the actual prompt content in the context window.
        """
        meta: dict[str, Any] = {}
        for key, value in self._namespace.items():
            if key.startswith("_") or callable(value):
                continue
            if isinstance(value, str):
                meta[key] = {
                    "type": "string",
                    "length": len(value),
                    "prefix": value[:100] + "..." if len(value) > 100 else value,
                }
            elif isinstance(value, (list, dict)):
                import json

                try:
                    s = json.dumps(value, default=str)
                    meta[key] = {
                        "type": type(value).__name__,
                        "length": len(value),
                        "prefix": s[:200] + "..." if len(s) > 200 else s,
                    }
                except (TypeError, ValueError):
                    meta[key] = {
                        "type": type(value).__name__,
                        "length": len(value) if hasattr(value, "__len__") else "?",
                    }
            else:
                meta[key] = {"type": type(value).__name__, "value": str(value)[:100]}
        return meta

    def get_variable(self, name: str) -> Any:
        """Retrieve a variable from the REPL namespace."""
        return self._namespace.get(name)

    def get_available_functions(self) -> list[str]:
        """List registered function names (for system prompt)."""
        return list(self._registered_functions.keys())

    # ----- Cell pinning / eviction (RLM paper §notebook interface) -----

    def pin_cell(self, n: int) -> str:
        """Mark cell `n` as non-evictable.

        Called by the agent from within the REPL (registered as
        `pin_cell`). Pinned cells survive auto-eviction and cannot be
        evicted by `evict_cell`. Pin the task prompt cell and any cells
        whose outputs you reference by name later in the run.
        """
        if n < 1 or n > self._turn_count:
            return f"[pin_cell] no such cell In[{n}]; have 1..{self._turn_count}"
        self._pinned_cells.add(n)
        self._evicted_cells.discard(n)
        return f"pinned In[{n}]"

    def evict_cell(self, n: int) -> str:
        """Mark cell `n` as evicted (replaced by a placeholder in history).

        Evicting frees context budget on long runs. Evicting a pinned
        cell is a no-op — unpin it first if you really mean to.
        """
        if n < 1 or n > self._turn_count:
            return f"[evict_cell] no such cell In[{n}]; have 1..{self._turn_count}"
        if n in self._pinned_cells:
            return f"[evict_cell] In[{n}] is pinned — unpin first"
        self._evicted_cells.add(n)
        return f"evicted In[{n}]"

    @property
    def pinned_cells(self) -> list[int]:
        return sorted(self._pinned_cells)

    @property
    def evicted_cells(self) -> list[int]:
        return sorted(self._evicted_cells)

    def auto_evict(self, max_turns_visible: int) -> list[int]:
        """Auto-evict the oldest unpinned cells until at most
        `max_turns_visible` cells are visible. Returns the list of newly
        evicted turn numbers.
        """
        visible = [
            m.turn_number
            for m in self._history
            if m.turn_number not in self._evicted_cells
        ]
        if len(visible) <= max_turns_visible:
            return []
        overflow = len(visible) - max_turns_visible
        newly_evicted: list[int] = []
        for n in visible:
            if overflow <= 0:
                break
            if n in self._pinned_cells:
                continue
            self._evicted_cells.add(n)
            newly_evicted.append(n)
            overflow -= 1
        return newly_evicted

    def format_history_for_llm(self) -> list[dict[str, str]]:
        """
        Format the metadata-only history as alternating assistant/user messages,
        using the RLM paper's Jupyter-style In[N]/Out[N] convention.

        Each turn becomes:
        - assistant: `In[N]:\\n```python\\n...```
        - user:      `Out[N]: ...metadata...`

        Evicted cells collapse to `[In[N] evicted]` placeholders on the
        user side (frees context while preserving the numbering so
        later references like "re-run In[2]" still resolve).

        This is the RLM context discipline: metadata-only, constant-size
        per turn, addressable by cell index.
        """
        messages: list[dict[str, str]] = []
        for meta in self._history:
            n = meta.turn_number
            if n in self._evicted_cells:
                # Keep the slot so numbering is stable; collapse content.
                messages.append({
                    "role": "assistant",
                    "content": f"In[{n}]: [evicted]",
                })
                messages.append({
                    "role": "user",
                    "content": f"Out[{n}]: [evicted]",
                })
                continue

            pin_marker = " [pinned]" if n in self._pinned_cells else ""
            messages.append({
                "role": "assistant",
                "content": f"In[{n}]{pin_marker}:\n```python\n{meta.code_executed}\n```",
            })
            result_parts = [f"Out[{n}]{pin_marker}:"]
            if meta.error:
                result_parts.append(f"ERROR: {meta.error[:300]}")
            else:
                result_parts.append("(executed ok)")
            if meta.stdout_length > 0:
                result_parts.append(
                    f"stdout ({meta.stdout_length} chars): {meta.stdout_prefix}"
                )
            if meta.variables_changed:
                result_parts.append(f"new vars: {meta.variables_changed}")
            messages.append({"role": "user", "content": " ".join(result_parts)})

        return messages
