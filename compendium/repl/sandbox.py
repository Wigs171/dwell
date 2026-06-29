"""Namespace allowlist for safe REPL code execution."""

from __future__ import annotations

import json
import math
import re
from typing import Any

# Builtins that are safe to expose in the REPL namespace
SAFE_BUILTINS: dict[str, Any] = {
    # Types
    "bool": bool,
    "int": int,
    "float": float,
    "str": str,
    "bytes": bytes,
    "list": list,
    "tuple": tuple,
    "dict": dict,
    "set": set,
    "frozenset": frozenset,
    "type": type,
    # Iteration
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "reversed": reversed,
    "sorted": sorted,
    # Math
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "round": round,
    "pow": pow,
    "divmod": divmod,
    # String/repr
    "repr": repr,
    "len": len,
    "hash": hash,
    "chr": chr,
    "ord": ord,
    "format": format,
    # Checks
    "isinstance": isinstance,
    "issubclass": issubclass,
    "callable": callable,
    "hasattr": hasattr,
    "getattr": getattr,
    # Comprehension helpers
    "any": any,
    "all": all,
    # Exceptions (agent code may need to handle errors)
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "StopIteration": StopIteration,
    # I/O (stdout is captured by the REPL environment)
    "print": print,
    # None/True/False are automatic in exec
    "None": None,
    "True": True,
    "False": False,
}

# Safe standard library modules
SAFE_MODULES: dict[str, Any] = {
    "json": json,
    "re": re,
    "math": math,
}


def build_namespace(
    context_vars: dict[str, Any] | None = None,
    registered_functions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a safe execution namespace for the REPL."""
    namespace: dict[str, Any] = {}

    # Safe builtins
    namespace["__builtins__"] = SAFE_BUILTINS

    # Safe modules
    namespace.update(SAFE_MODULES)

    # Context variables (topic, source material, etc.)
    if context_vars:
        namespace.update(context_vars)

    # Registered functions (llm_query, web_search, etc.)
    if registered_functions:
        namespace.update(registered_functions)

    return namespace
