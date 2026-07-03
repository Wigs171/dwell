"""Curated Paths — load/validate the `_meta/paths/*.json` recipes.

A Path is a *recipe, not a vault* (see `DWELL_PATHS.md`): an ordered spine of
anchor node-ids + a lens, rendered live over the untouched graph. This module is
just the loader/validator; the `PathNavigator` (dwell.py) walks the spine and the
server applies the lens.

Phase 0: a frozen spine of `read` gates, no confluence frames. Only the first
(main) arc contributes to the spine. Anchor ids are normalized to lowercase (the
graph stores wikilink targets lowercased).
"""

from __future__ import annotations

import json
import math
from pathlib import Path


def _paths_dir(vault) -> Path:
    return vault.meta / "paths"


def _spine_ids(data: dict) -> list[str]:
    """Ordered anchor node-ids from the path's main arc (Phase 0: one arc)."""
    ids: list[str] = []
    for arc in data.get("arcs") or []:
        if not isinstance(arc, dict):
            continue
        for gate in arc.get("gates") or []:
            if isinstance(gate, dict) and gate.get("anchor"):
                ids.append(str(gate["anchor"]).strip().lower())
        if ids:              # Phase 0: only the first non-empty arc is the spine
            break
    return ids


def list_paths(vault) -> list[dict]:
    """`[{id, title, goal, gates}]` for every `_meta/paths/*.json`."""
    d = _paths_dir(vault)
    out: list[dict] = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        pid = str(data.get("id") or f.stem)
        out.append({
            "id": pid,
            "title": data.get("title") or pid,
            "goal": data.get("goal") or "",
            "gates": len(_spine_ids(data)),
        })
    return out


def load_path(vault, path_id: str) -> dict | None:
    """Load one path by id (filename stem or the `id` field). Adds a normalized
    `spine` list. Returns None if missing/unparseable."""
    d = _paths_dir(vault)
    if not d.is_dir():
        return None
    cand = d / f"{path_id}.json"
    files = [cand] if cand.is_file() else list(sorted(d.glob("*.json")))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if f == cand or str(data.get("id") or f.stem) == path_id:
            data["spine"] = _spine_ids(data)
            data.setdefault("id", f.stem)
            return data
    return None


def resolve_spine(data: dict, node_ids) -> tuple[list[str], list[str]]:
    """Split the spine into (resolved, missing) against the live vault's node-id
    set — the minimal 'path lint'. Broken anchors are dropped, order preserved."""
    have = set(node_ids)
    spine = data.get("spine") or _spine_ids(data)
    resolved = [n for n in spine if n in have]
    missing = [n for n in spine if n not in have]
    return resolved, missing


# ---------------------------------------------------------------------------
# The spine generator — wander → narrativize (DWELL_PATHS.md).
# Diversity comes from the STOCHASTIC GRAPH WALK, not the LLM: sample two
# endpoints broadly (centrality a POSITIVE weight, recency-discounted, SAMPLED
# not argmax — so hubs recur *often, not always*, never penalized), then route
# between them by SEMANTIC CONTINUITY (theme, not shortest-hop) with a noised
# heading + Boltzmann-sampled steps, snapping every step to a real node. This
# is the mechanical, $0 core; the server names the arc (LLM when live, else a
# mechanical title). No hub penalty anywhere.
# ---------------------------------------------------------------------------

def _weighted_choice(items: list, weights: list, rng) -> str:
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    r = rng.random() * total
    acc = 0.0
    for it, w in zip(items, weights):
        acc += w
        if r <= acc:
            return it
    return items[-1]


def _sample_start(brain, rng, avoid, history) -> str:
    ids = brain.ids
    w = []
    for n in ids:
        x = brain.centrality(n) + 1.0                 # centrality is a POSITIVE weight
        if history is not None:
            x = max(0.25, x - 3.0 * history.seen_count(n))   # recency discount → "not always"
        if avoid and n in avoid:
            x *= 0.05
        w.append(x)
    return _weighted_choice(ids, w, rng)


def _sample_dest(brain, rng, start, avoid, history) -> str:
    sp = brain.space
    svec = sp.vec(start)
    ids = [n for n in brain.ids if n != start]
    if not ids:
        return start
    if len(ids) > 400:                                # keep generation snappy on big vaults
        ids = rng.sample(ids, 400)
    w = []
    for n in ids:
        far = 1.0 - max(0.0, min(1.0, sp.cos(svec, sp.vec(n))))   # span: farther ⇒ likelier
        x = (brain.centrality(n) + 1.0) * (0.35 + far)
        if history is not None:
            x = max(0.25, x - 3.0 * history.seen_count(n))
        if avoid and n in avoid:
            x *= 0.05
        w.append(x)
    return _weighted_choice(ids, w, rng)


def _route(brain, rng, start, dest, length, temperature) -> list[str]:
    sp = brain.space
    dvec = sp.vec(dest)
    path = [start]
    visited = {start}
    cur = start
    for _ in range(length * 4):
        if cur == dest or len(path) >= length:
            break
        heading = sp.blend(sp.vec(cur), dvec, 0.55)          # head toward the arrival
        try:                                                 # on-manifold noise: nudge toward a random real node
            heading = sp.blend(heading, sp.vec(rng.choice(brain.ids)), 0.25 * temperature)
        except Exception:
            pass
        pool = dict(sp.neighbors(cur, topk=16))
        for lid in brain.nodes[cur].out_links:
            pool.setdefault(lid, 0.0)
        cands = [(sp.cos(heading, sp.vec(c)), c) for c in pool if c not in visited and c != cur]
        if not cands:
            break
        cands.sort(reverse=True)
        top = cands[:6]
        temp = max(0.05, temperature)
        wts = [math.exp(s / temp) for s, _ in top]           # Boltzmann-sample the step (τ)
        nxt = _weighted_choice([c for _, c in top], wts, rng)
        path.append(nxt); visited.add(nxt); cur = nxt
    if cur != dest and len(path) < length and dest not in visited:
        path.append(dest)
    return path


def _coherence(brain, path) -> float:
    sp = brain.space
    if len(path) < 2:
        return 0.0
    sims = [max(0.0, sp.cos(sp.vec(path[i]), sp.vec(path[i + 1]))) for i in range(len(path) - 1)]
    return sum(sims) / len(sims)


def generate_spine(brain, rng, *, length: int = 5, temperature: float = 0.6,
                   history=None, avoid=None, candidates: int = 3) -> list[str]:
    """Return an ordered list of node-ids forming a coherent-but-diverse arc.
    Generates `candidates` walks and keeps the most arc-like (mean adjacent
    similarity, mild length reward). Mechanical + $0. Diversity from the walk."""
    ids = brain.ids
    if len(ids) <= max(2, length - 1):
        return list(ids)
    best, best_score = None, -1.0
    for _ in range(max(1, candidates)):
        start = _sample_start(brain, rng, avoid, history)
        dest = _sample_dest(brain, rng, start, avoid, history)
        route = _route(brain, rng, start, dest, length, temperature)
        if len(route) < 3:
            continue
        score = _coherence(brain, route) + 0.05 * len(route)
        if score > best_score:
            best, best_score = route, score
    return best or [_sample_start(brain, rng, avoid, history)]
