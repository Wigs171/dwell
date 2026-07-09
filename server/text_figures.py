"""Derived TEXT-figures for Dwell pages — the engine side of DWELL_TEXT_FIGURES_PLAN.md.

A "figure" generalizes to image-OR-text: where a page has no image, the scheduler may
place a derived text-figure (a pull-quote, a drop-cap, …) into the same slot. This module
decides WHICH text-figure (if any) a page carries, gated so versatile figures never
over-represent:

  eligibility = form-affinity  ×  content-affordance
  placement   = deterministic by (node, page-ordinal) + a density dial

Slice 1 implements the two ZERO-TOKEN figures (no extra model call):
  • drop-cap   — opening flourish; pure CSS ::first-letter in the reader (no payload).
  • pull-quote — a verbatim striking line lifted from the page text (the reader floats it,
                 aria-hidden since it duplicates body text).
Later slices add model-/enrichment-derived figures (key-takeaways, callout, …); they slot
into the same `choose_text_figure` candidate logic.

The form-affinity table MIRRORS `dwell-web/src/lib/figureForms.ts` (keep them in sync).
"""
from __future__ import annotations

import hashlib
import re

# native = home form (scheduler boost); allowed = fine; anything else = blocked.
# Mirror of figureForms.ts (only the IMPLEMENTED kinds need be correct for now).
_AFFINITY: dict[str, dict[str, set[str]]] = {
    "drop-cap":   {"native": {"article"}, "allowed": set()},
    "pull-quote": {"native": {"article"}, "allowed": {"guided", "dialogue"}},
    "stepped-list": {"native": {"tutorial"}, "allowed": {"guided", "article"}},
    # (the rest land as their reader support ships)
    "key-takeaways": {"native": {"guided"}, "allowed": {"article", "qa"}},
    "callout":       {"native": {"guided"}, "allowed": {"article", "qa"}},
}

# Reader support has shipped for these (engine won't emit a figure the reader can't draw).
IMPLEMENTED: tuple[str, ...] = ("drop-cap", "pull-quote", "stepped-list")

# Density → spacing between text-figure pages (every Nth eligible page). 'off' disables.
_PERIOD = {"off": 0, "sparse": 4, "normal": 3, "rich": 2}
DEFAULT_DENSITY = "sparse"


def fits_form(kind: str, form: str) -> bool:
    """True unless the figure is BLOCKED in this form (the hard rule)."""
    a = _AFFINITY.get(kind)
    if a is None:
        return True
    return form in a["native"] or form in a["allowed"]


def _stable_hash(s: str) -> int:
    """Deterministic across runs (unlike salted hash()), so re-pitch is stable."""
    return int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)


def _pick_quote(text: str) -> str | None:
    """The single most pull-worthy VERBATIM line: a self-contained mid-length
    sentence (40–140 chars), preferring not the very first one (the opening).
    Deterministic (longest in range) so re-pitch at the same text is stable."""
    sents = [s.strip().lstrip("-—•*").strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip())]
    # a real, self-contained sentence: starts with a capital, no colon/list residue,
    # mid-length. The capital-start guard rejects fragments ("300 CE) …", "- and so on").
    cand = [s for s in sents if 40 <= len(s) <= 140 and ":" not in s and s[:1].isupper()]
    if len(cand) > 1:
        cand = cand[1:]                       # skip the opening sentence when we can
    return max(cand, key=len) if cand else None


def choose_text_figure(
    page_text: str,
    form: str,
    pos: int,
    node_id: str,
    *,
    has_image: bool = False,
    density: str = DEFAULT_DENSITY,
    steps: list[str] | None = None,
) -> dict | None:
    """Pick the text-figure for this page, or None.

    `pos` is the page's stable ordinal on its node (see `_node_page_pos`) — so the choice
    is deterministic and survives re-leveling/coast/repage. Images win the slot: if the
    page already shows a picture, no text-figure. Form-affinity is the hard gate; density +
    the node hash space the figures out and vary them across nodes.

    `steps` (caller-derived, e.g. Renderer.derive_steps on a tutorial keyframe) short-
    circuits into a stepped-list panel: it is the lesson's SKELETON, not decoration, so it
    bypasses the density cadence — only images and form-affinity outrank it.
    """
    if has_image:
        return None
    if steps and "stepped-list" in IMPLEMENTED and fits_form("stepped-list", form):
        return {"kind": "stepped-list", "slot": "panel",
                "payload": {"steps": [str(s) for s in steps][:5]}}
    period = _PERIOD.get(density, 0)
    if period == 0:
        return None
    h = _stable_hash(node_id)

    # Does this page carry a figure AT ALL? One sparse cadence governs everything.
    # `pos` is the per-NODE ordinal, and a graph-walking reader lands on a fresh node
    # almost every page (pos == 0), so this gates by node hash → ~1/period of DISTINCT
    # nodes get a figure, NOT every node-opening. (The old code special-cased pos == 0
    # for the drop-cap, which — since pos == 0 is nearly always true — put a drop-cap on
    # ~half of all pages and ignored the density dial entirely.)
    if (pos + h) % period != 0:
        return None

    # On a figure page, the drop-cap is the RARE opening flourish: a node's first page,
    # article-form, and only a third of the figure-nodes (≈ 1/(3·period) of pages overall,
    # e.g. ~1/12 at sparse). A SEPARATE salted roll (not `h`) so it never collides with the
    # cadence gate above — using `h % 3` would make every figure-page a drop-cap at period 3.
    # Everything else gets a pull-quote.
    if (pos == 0 and "drop-cap" in IMPLEMENTED and fits_form("drop-cap", form)
            and _stable_hash(node_id + "#dropcap") % 3 == 0):
        return {"kind": "drop-cap", "slot": "body", "payload": {}}

    if "pull-quote" in IMPLEMENTED and fits_form("pull-quote", form):
        quote = _pick_quote(page_text)
        if quote:
            return {"kind": "pull-quote", "slot": "side", "payload": {"text": quote}}

    return None
