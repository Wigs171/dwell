"""Universal ingest enrichment — the upstream substrate investment.

ONE content-agnostic pass that extracts the structured data every Tier-2 transform
needs (timeline, comparison/concept-map, study-guide/quiz, glossary), writing the
`_meta/` sidecars. See prototypes/DWELL_ENRICH_PLAN.md for the full design.

PHASE A — MECHANICAL CORE (this module; $0, no LLM, runs on every node):
  • graph + salience  — wikilinks → edges (untyped) + in-degree centrality
  • contradicts edges — merged from the contradiction-ledger
  • temporal anchors  — dates/periods regex'd from bodies (drives the timeline)
  • terms → gloss     — every entity/concept page's title+summary+aliases
  • quote-claims      — reuse the grounding engine (claims with provenance+verdict)

PHASE B — BOUNDED LLM PASS (top-N nodes by centrality, content-hash-gated, budget-capped):
  • typed edges    — labels each wikilink with a PREDICATE (precedes, causes, part-of, …)
  • atomic claims  — lifts verifiable propositions (provenance + salience)
  • semantic axes  — domain-neutral data points per page: stance, viewpoints, analogies,
                     symbols, procedures, stages, parts, functions, caveats, quantities,
                     places, definitions, questions, difficulty (stance/viewpoint always
                     attributed to its holder). See `extract_axes_llm()`.

The sidecars use the same atomic write as `contradiction-ledger.json`
(tmp file + os.replace), schema-versioned, keyed by page id (temporal is a flat
sorted event list). Idempotent: re-running recomputes Phase A fresh.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field

from compendium.models import PageType
from compendium.vault import VaultPaths, list_pages, read_page, today_iso
from compendium.vault.links import build_alias_index, parse_wikilinks, resolve_target
from compendium.vault.contradiction_ledger import ContradictionLedger

_SCHEMA_VERSION = 1

# The small, universal, extensible predicate set (Phase B types edges into it).
# Phase A leaves wikilink edges untyped (type=None) and only stamps `contradicts`
# from the ledger.
PREDICATES = (
    "precedes", "requires", "influences", "derives-from", "part-of",
    "instance-of", "contradicts", "causes", "exemplifies",
)


# ---------------------------------------------------------------------------
# Serializable records
# ---------------------------------------------------------------------------
@dataclass
class Edge:
    target: str
    type: str | None = None        # a PREDICATE, "related", or None (untyped wikilink)
    via: str = "wikilink"          # wikilink | ledger | llm
    conf: float | None = None
    evidence: str = ""

    def to_dict(self) -> dict:
        d: dict = {"target": self.target, "type": self.type, "via": self.via}
        if self.conf is not None:
            d["conf"] = round(self.conf, 3)
        if self.evidence:
            d["evidence"] = self.evidence
        return d


@dataclass
class TemporalAnchor:
    page: str
    kind: str                      # date | period
    text: str
    year: int | None = None        # sortable representative year (BCE negative)
    start: int | None = None
    end: int | None = None
    conf: float = 0.9
    via: str = "regex"

    def to_dict(self) -> dict:
        d: dict = {"page": self.page, "kind": self.kind, "text": self.text,
                   "year": self.year, "conf": round(self.conf, 2), "via": self.via}
        if self.start is not None:
            d["start"] = self.start
        if self.end is not None:
            d["end"] = self.end
        return d


@dataclass
class Claim:
    text: str
    kind: str = "quote"            # quote | proposition
    provenance: list[str] = field(default_factory=list)
    grounding: str = ""            # grounded | loose | not-found | unverifiable
    score: float | None = None
    salience: float | None = None  # LLM-rated importance (propositions)
    via: str = "grounding"         # grounding | llm

    def to_dict(self) -> dict:
        d: dict = {"text": self.text, "kind": self.kind,
                   "provenance": self.provenance, "via": self.via}
        if self.grounding:
            d["grounding"] = self.grounding
        if self.score is not None:
            d["score"] = round(self.score, 2)
        if self.salience is not None:
            d["salience"] = round(self.salience, 2)
        return d


@dataclass
class Term:
    term: str
    page: str
    gloss: str = ""
    aliases: list[str] = field(default_factory=list)
    salience: float = 0.0

    def to_dict(self) -> dict:
        return {"term": self.term, "page": self.page, "gloss": self.gloss,
                "aliases": self.aliases, "salience": round(self.salience, 3)}


# ---------------------------------------------------------------------------
# Phase B semantic axes — domain-neutral data points lifted from each page's prose
# ---------------------------------------------------------------------------
# Each axis recurs across domains (history, cooking, code, scholarship) and unlocks
# a derived view. The LLM returns one record-set per page; list axes are capped and
# unknown keys / empties are dropped. `difficulty` is a single object, not a list.
AXIS_SPEC: dict[str, tuple[str, ...]] = {
    "quantities":  ("value", "unit", "of"),                       # measured values
    "places":      ("name", "role"),                              # spatial anchors
    "definitions": ("term", "gloss"),                             # in-body term -> meaning
    "stances":     ("toward", "polarity", "holder", "evidence"),  # endorse/reject/qualify
    "viewpoints":  ("position", "holder", "school"),              # who-holds-what
    "analogies":   ("source", "target", "mapping"),               # this-is-like-that
    "symbols":     ("symbol", "meaning"),                         # sign -> referent
    "procedures":  ("name", "steps"),                             # ordered actionable steps
    "stages":      ("process", "sequence"),                       # lifecycle / state-transitions
    "parts":       ("whole", "parts", "cardinality"),             # part-whole enumeration
    "functions":   ("thing", "purpose"),                          # teleology (what X is for)
    "caveats":     ("claim", "condition"),                        # scope / exceptions
    "questions":   (),                                            # flat list of question strings
}
_LIST_SUBFIELDS = {"steps", "sequence", "parts", "prerequisites"}
_AXIS_CAP = 6
_POLARITIES = ("endorse", "reject", "qualify", "neutral")
_LEVELS = ("foundational", "intermediate", "advanced")


@dataclass
class PageAxes:
    """The semantic axes lifted from one page. `data` maps an axis name to a list of
    small dict records (or, for `questions`, a list of strings; for `difficulty`, a
    single dict). Empty axes are absent."""
    content_hash: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.data.items() if v}

    @property
    def count(self) -> int:
        n = 0
        for v in self.data.values():
            n += len(v) if isinstance(v, list) else 1
        return n


@dataclass
class NodeEnrichment:
    in_degree: int = 0
    out_degree: int = 0
    centrality: float = 0.0
    content_hash: str = ""
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"in_degree": self.in_degree, "out_degree": self.out_degree,
                "centrality": round(self.centrality, 3), "content_hash": self.content_hash,
                "edges": [e.to_dict() for e in self.edges]}


@dataclass
class EnrichmentResult:
    timestamp: str = ""
    topic: str = ""
    method: str = "mechanical"
    nodes: dict[str, NodeEnrichment] = field(default_factory=dict)
    temporal: list[TemporalAnchor] = field(default_factory=list)
    claims: dict[str, list[Claim]] = field(default_factory=dict)
    terms: dict[str, Term] = field(default_factory=dict)
    claims_grounded: bool = False     # whether the grounding pass ran (claims present)
    axes: dict[str, "PageAxes"] = field(default_factory=dict)   # Phase B semantic axes
    axes_extracted: bool = False

    # headline counts (for the report + CLI)
    @property
    def page_count(self) -> int:
        return len(self.nodes)

    @property
    def axis_count(self) -> int:
        return sum(a.count for a in self.axes.values())

    @property
    def edge_count(self) -> int:
        return sum(len(n.edges) for n in self.nodes.values())

    @property
    def typed_edge_count(self) -> int:
        return sum(1 for n in self.nodes.values() for e in n.edges if e.type)

    @property
    def claim_count(self) -> int:
        return sum(len(v) for v in self.claims.values())


# ---------------------------------------------------------------------------
# Temporal extraction (mechanical regex — drives the timeline view)
# ---------------------------------------------------------------------------
_ERA = r"(BCE|BC|CE|AD)"
# Order matters: try the most specific shapes first, remove matched spans so a
# range isn't also caught as two bare years.
_RANGE_ERA_RE = re.compile(
    rf"\b(?:c\.?\s*)?(\d{{1,4}})\s*[-–—]\s*(\d{{1,4}})\s*{_ERA}\b", re.IGNORECASE)
_CENTURY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)\s+(?:century|c\.)\s*{_ERA}?\b", re.IGNORECASE)
_YEAR_ERA_RE = re.compile(rf"\b(?:c\.?\s*)?(\d{{1,4}})\s*{_ERA}\b", re.IGNORECASE)
_BARE_YEAR_RE = re.compile(r"\b(1\d{3}|20\d{2})\b")        # 1000–2099, assumed CE


def _signed(year: int, era: str | None) -> int:
    return -year if (era and era.upper() in ("BCE", "BC")) else year


def _extract_temporal(page_id: str, body: str, cur_year: int = 9999) -> list[TemporalAnchor]:
    out: list[TemporalAnchor] = []
    seen: set[str] = set()
    text = body
    spans: list[tuple[int, int]] = []

    def _take(m) -> bool:
        # reject if overlapping an already-consumed (more specific) span
        for s, e in spans:
            if m.start() < e and m.end() > s:
                return False
        spans.append((m.start(), m.end()))
        return True

    for m in _RANGE_ERA_RE.finditer(text):
        if not _take(m):
            continue
        a, b, era = int(m.group(1)), int(m.group(2)), m.group(3)
        start, end = _signed(a, era), _signed(b, era)
        key = m.group(0).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(TemporalAnchor(page_id, "period", m.group(0).strip(),
                                  year=min(start, end), start=min(start, end),
                                  end=max(start, end), conf=0.92))
    for m in _CENTURY_RE.finditer(text):
        if not _take(m):
            continue
        n, era = int(m.group(1)), m.group(2)
        # nth century CE → years (n-1)*100+1 .. n*100 ; BCE mirrored & negated
        hi, lo = n * 100, (n - 1) * 100 + 1
        if era and era.upper() in ("BCE", "BC"):
            start, end = -hi, -lo
        else:
            start, end = lo, hi
        key = m.group(0).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(TemporalAnchor(page_id, "period", m.group(0).strip(),
                                  year=start, start=start, end=end, conf=0.8))
    for m in _YEAR_ERA_RE.finditer(text):
        if not _take(m):
            continue
        y = _signed(int(m.group(1)), m.group(2))
        key = m.group(0).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(TemporalAnchor(page_id, "date", m.group(0).strip(), year=y, conf=0.9))
    for m in _BARE_YEAR_RE.finditer(text):
        if not _take(m):
            continue
        y = int(m.group(1))
        # Drop "now" and future years: they're authoring/citation/access-date
        # metadata, not historical events. Content-neutral (a modern vault keeps
        # its real past years; only ≥ the authoring year is excluded).
        if y >= cur_year:
            continue
        key = m.group(0).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(TemporalAnchor(page_id, "date", m.group(0).strip(), year=y, conf=0.55))
    return out


def _content_hash(title: str, body: str) -> str:
    return hashlib.sha1((title + "\n" + body).encode("utf-8")).hexdigest()[:16]


def _first_sentence(text: str) -> str:
    t = text.strip()
    m = re.search(r"(?<=[.!?])\s", t)
    return (t[: m.start() + 1] if m else t)[:280].strip()


def _read_topic(vault: VaultPaths) -> str:
    try:
        for line in vault.claude_md.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return vault.root.name


# ---------------------------------------------------------------------------
# The mechanical enrichment pass
# ---------------------------------------------------------------------------
def enrich_vault(vault: VaultPaths, *, ground: bool = True) -> EnrichmentResult:
    """Phase A: extract the free structured layers from a vault.

    `ground` runs the grounding engine for the claims layer (mechanical, but reads
    raw sources incl. PDFs — set False to skip on very large vaults).
    """
    result = EnrichmentResult(timestamp=today_iso(), topic=_read_topic(vault),
                              method="mechanical")
    alias_map = build_alias_index(vault)
    page_ids = list_pages(vault)
    try:
        cur_year = int(result.timestamp[:4])
    except (ValueError, IndexError):
        cur_year = 9999

    # Single pass: read each page once → edges (out), terms, temporal; tally in-degree.
    in_degree: dict[str, int] = {pid: 0 for pid in page_ids}
    for pid in page_ids:
        page = read_page(vault, pid)
        if page is None:
            continue
        node = NodeEnrichment(content_hash=_content_hash(page.title, page.body))

        seen_targets: set[str] = set()
        for link in parse_wikilinks(page.body):
            canonical = resolve_target(link.target, alias_map)
            if not canonical or canonical == pid or canonical in seen_targets:
                continue
            seen_targets.add(canonical)
            node.edges.append(Edge(target=canonical, type=None, via="wikilink"))
            in_degree[canonical] = in_degree.get(canonical, 0) + 1
        node.out_degree = len(node.edges)
        result.nodes[pid] = node

        # terms — entity/concept pages are the vault's defined vocabulary
        if page.type in (PageType.ENTITY, PageType.CONCEPT):
            gloss = page.summary.strip() or _first_sentence(page.body)
            result.terms[pid] = Term(term=page.title, page=pid, gloss=gloss,
                                     aliases=list(page.aliases))

        # temporal anchors
        anchors = _extract_temporal(pid, page.body, cur_year)
        if anchors:
            result.temporal.extend(anchors)

    # in-degree + normalized centrality
    max_in = max(in_degree.values(), default=0) or 1
    for pid, node in result.nodes.items():
        node.in_degree = in_degree.get(pid, 0)
        node.centrality = node.in_degree / max_in
    for pid, term in result.terms.items():
        term.salience = result.nodes[pid].centrality if pid in result.nodes else 0.0

    # contradicts edges from the ledger (bidirectional between the tension's pages)
    try:
        ledger = ContradictionLedger(vault).load()
        for entry in ledger.values():
            if entry.status == "resolved":
                continue
            for a in entry.pages:
                if a not in result.nodes:
                    continue
                for b in entry.pages:
                    if a == b or b not in result.nodes:
                        continue
                    if not any(e.target == b and e.type == "contradicts"
                               for e in result.nodes[a].edges):
                        result.nodes[a].edges.append(
                            Edge(target=b, type="contradicts", via="ledger", conf=1.0))
    except Exception:
        pass

    # temporal sorted by representative year (events with a year first)
    result.temporal.sort(key=lambda t: (t.year is None, t.year if t.year is not None else 0))

    # claims — reuse the grounding engine (quotes + provenance + verdict), best-effort
    if ground:
        try:
            from compendium.agents.grounding import ground_vault
            report = ground_vault(vault)
            for chk in report.checks:
                result.claims.setdefault(chk.page_id, []).append(
                    Claim(text=chk.quote, kind="quote",
                          provenance=[chk.source_matched] if chk.source_matched else [],
                          grounding=chk.status, score=chk.score, via="grounding"))
            result.claims_grounded = True
        except Exception:
            result.claims_grounded = False

    return result


# ---------------------------------------------------------------------------
# Phase B — bounded LLM edge-typing + proposition extraction (high-salience nodes)
# ---------------------------------------------------------------------------
_TYPING_SYSTEM = (
    "You label the relationships a wiki page asserts toward the pages it links to, "
    "and extract its most important atomic claims. You respond with a SINGLE JSON "
    "object and nothing else — no prose, no code fences."
)


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t[3:]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


def _build_typing_prompt(page, targets: list[dict]) -> str:
    preds = ", ".join(PREDICATES)
    tlist = "\n".join(
        f"- {t['id']}: {t['title']}" + (f" — {t['summary']}" if t['summary'] else "")
        for t in targets
    )
    return (
        f"PAGE: {page.title}\n{page.body[:3500]}\n\n"
        f"This page links to the pages below. For EACH, choose the ONE relationship "
        f"this page (\"{page.title}\") asserts TOWARD the linked page, from this set:\n"
        f"  {preds}\n"
        f"If none clearly fits, use \"related\".\n\n"
        f"LINKED PAGES:\n{tlist}\n\n"
        f"Also extract 2–5 ATOMIC, verifiable claims THIS page makes (one proposition each).\n\n"
        f"Return JSON EXACTLY in this shape:\n"
        f'{{"edges":[{{"target":"<id from the list>","type":"<predicate or related>",'
        f'"conf":0.0,"evidence":"<short phrase from the page>"}}],'
        f'"claims":[{{"text":"<one atomic proposition>","salience":0.0}}]}}\n\n'
        f"RULES (obey exactly):\n"
        f"- Use ONLY the target ids listed above; at most one entry per linked page.\n"
        f"- Choose \"related\" when no predicate clearly fits — do NOT force one.\n"
        f"- 'evidence' must be a short phrase grounded in the page text.\n"
        f"- Claims must be atomic (a single fact) and asserted by THIS page."
    )


def _load_prior_typing(vault: VaultPaths):
    """Prior LLM-typed edges + propositions + content hashes (for idempotent re-runs)."""
    prior_hash: dict[str, str] = {}
    prior_edges: dict[str, dict[str, dict]] = {}
    prior_props: dict[str, list[dict]] = {}
    try:
        g = json.loads(vault.enrichment_graph_json.read_text(encoding="utf-8"))
        for pid, n in (g.get("nodes") or {}).items():
            prior_hash[pid] = n.get("content_hash", "")
            te = {e["target"]: e for e in (n.get("edges") or [])
                  if e.get("type") and e.get("via") == "llm"}
            if te:
                prior_edges[pid] = te
    except (OSError, json.JSONDecodeError):
        pass
    try:
        c = json.loads(vault.enrichment_claims_json.read_text(encoding="utf-8"))
        for pid, cs in (c.get("pages") or {}).items():
            props = [x for x in cs if x.get("via") == "llm"]
            if props:
                prior_props[pid] = props
    except (OSError, json.JSONDecodeError):
        pass
    return prior_hash, prior_edges, prior_props


def type_edges_llm(
    vault: VaultPaths,
    result: EnrichmentResult,
    *,
    client,
    model: str = "claude-haiku-4-5",
    cost_tracker,
    top_frac: float = 0.2,
    top_n: int | None = None,
    max_targets: int = 24,
    progress=None,
) -> dict:
    """Phase B: type the untyped wikilink edges + lift atomic propositions on the
    top-N nodes by centrality. Mutates `result`. Content-hash-gated against the
    sidecars already on disk so re-runs only call the LLM for new/changed nodes.
    Stops cleanly when the cost budget is hit (rest stay untyped)."""
    prior_hash, prior_edges, prior_props = _load_prior_typing(vault)

    cand = [(pid, n) for pid, n in result.nodes.items()
            if any(e.type is None for e in n.edges)]
    cand.sort(key=lambda kv: -kv[1].centrality)
    k = top_n if top_n is not None else max(1, math.ceil(top_frac * max(1, len(result.nodes))))
    cand = cand[:k]

    stats = {"selected": len(cand), "llm_calls": 0, "reused": 0,
             "typed_edges": 0, "props": 0, "stopped_budget": False}

    for pid, node in cand:
        # idempotent reuse: unchanged content + prior typed edges → carry them over
        if prior_hash.get(pid) == node.content_hash and pid in prior_edges:
            te = prior_edges[pid]
            for e in node.edges:
                if e.type is None and e.target in te:
                    pe = te[e.target]
                    e.type, e.via = pe.get("type"), "llm"
                    e.conf, e.evidence = pe.get("conf"), pe.get("evidence", "")
                    stats["typed_edges"] += 1
            for p in prior_props.get(pid, []):
                result.claims.setdefault(pid, []).append(
                    Claim(text=p.get("text", ""), kind="proposition",
                          provenance=p.get("provenance", []),
                          salience=p.get("salience"), via="llm"))
                stats["props"] += 1
            stats["reused"] += 1
            continue

        try:
            cost_tracker.check_budget()
        except Exception:
            stats["stopped_budget"] = True
            break

        page = read_page(vault, pid)
        if page is None:
            continue
        targets = [e.target for e in node.edges if e.type is None][:max_targets]
        tinfo = []
        for t in targets:
            tp = read_page(vault, t)
            tinfo.append({"id": t, "title": tp.title if tp else t,
                          "summary": (tp.summary if tp else "")[:160]})
        if progress:
            progress(pid, len(tinfo))
        try:
            resp = client.messages.create(
                model=model, max_tokens=1500, system=_TYPING_SYSTEM,
                messages=[{"role": "user", "content": _build_typing_prompt(page, tinfo)}])
            cost_tracker.record_call(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                model=model, is_sub_call=True)
            data = _parse_json(resp.content[0].text)
        except Exception:
            continue
        stats["llm_calls"] += 1

        by_target = {e.target: e for e in node.edges if e.type is None}
        for ed in (data.get("edges") or []):
            e = by_target.get(ed.get("target"))
            if e is None:
                continue
            typ = ed.get("type") or "related"
            if typ not in PREDICATES and typ != "related":
                typ = "related"
            e.type, e.via = typ, "llm"
            try:
                e.conf = float(ed.get("conf"))
            except (TypeError, ValueError):
                e.conf = 0.7
            e.evidence = str(ed.get("evidence") or "")[:160]
            stats["typed_edges"] += 1
        for cl in (data.get("claims") or [])[:6]:
            txt = str(cl.get("text") or "").strip()
            if not txt:
                continue
            try:
                sal = float(cl.get("salience"))
            except (TypeError, ValueError):
                sal = None
            result.claims.setdefault(pid, []).append(
                Claim(text=txt, kind="proposition", provenance=list(page.sources),
                      salience=sal, via="llm"))
            stats["props"] += 1

    result.method = "hybrid"
    return stats


# ---------------------------------------------------------------------------
# Phase B (unified) — one LLM call/page: type edges + lift propositions + axes
# ---------------------------------------------------------------------------
_AXES_SYSTEM = (
    "You extract structured data a wiki page asserts: the typed relationships it draws "
    "to linked pages, its atomic claims, and a set of domain-neutral semantic axes "
    "(only those actually present). For evaluative or contested material you ALWAYS "
    "attribute the position to who holds it — you never restate a contested claim as "
    "plain fact. You respond with a SINGLE JSON object and nothing else — no prose, no "
    "code fences."
)

_AXES_SHAPE = (
    '{"edges":[{"target":"<id>","type":"<predicate|related>","conf":0.0,"evidence":"<phrase>"}],'
    '"claims":[{"text":"<atomic proposition>","salience":0.0}],'
    '"axes":{'
    '"quantities":[{"value":"","unit":"","of":""}],'
    '"places":[{"name":"","role":""}],'
    '"definitions":[{"term":"","gloss":""}],'
    '"stances":[{"toward":"","polarity":"endorse|reject|qualify|neutral","holder":"","evidence":""}],'
    '"viewpoints":[{"position":"","holder":"","school":""}],'
    '"analogies":[{"source":"","target":"","mapping":""}],'
    '"symbols":[{"symbol":"","meaning":""}],'
    '"procedures":[{"name":"","steps":[]}],'
    '"stages":[{"process":"","sequence":[]}],'
    '"parts":[{"whole":"","parts":[],"cardinality":0}],'
    '"functions":[{"thing":"","purpose":""}],'
    '"caveats":[{"claim":"","condition":""}],'
    '"questions":[],'
    '"difficulty":{"level":"foundational|intermediate|advanced","prerequisites":[]}}}'
)


def _build_extract_prompt(page, targets: list[dict]) -> str:
    preds = ", ".join(PREDICATES)
    if targets:
        tlist = "\n".join(
            f"- {t['id']}: {t['title']}" + (f" — {t['summary']}" if t['summary'] else "")
            for t in targets)
        edges_block = (
            f"1) EDGES — this page links to the pages below. For EACH, choose the ONE "
            f"relationship this page asserts TOWARD it from: {preds} (or \"related\" if "
            f"none fits). Use ONLY these ids, at most one per page:\n{tlist}\n\n")
    else:
        edges_block = "1) EDGES — none (return an empty edges list).\n\n"
    return (
        f"PAGE: {page.title}\n{page.body[:3500]}\n\n"
        f"Extract what THIS page asserts.\n\n"
        f"{edges_block}"
        f"2) CLAIMS — 2-5 atomic, verifiable propositions this page makes.\n\n"
        f"3) AXES — domain-neutral data points. Include an axis ONLY if the page truly "
        f"contains it; OMIT empty axes; never invent. Cap each list at {_AXIS_CAP}.\n"
        f"   quantities {{value,unit,of}} · places {{name,role}} · "
        f"definitions {{term,gloss}} · stances {{toward,polarity,holder,evidence}} · "
        f"viewpoints {{position,holder,school}} · analogies {{source,target,mapping}} · "
        f"symbols {{symbol,meaning}} · procedures {{name,steps[]}} · "
        f"stages {{process,sequence[]}} · parts {{whole,parts[],cardinality}} · "
        f"functions {{thing,purpose}} · caveats {{claim,condition}} · "
        f"questions [strings] · difficulty {{level,prerequisites[]}}\n\n"
        f"ATTRIBUTION (critical): for stances and viewpoints, name the HOLDER who asserts "
        f"the position (default: this page's author or subject). Polarity is one of "
        f"endorse|reject|qualify|neutral. Difficulty level is foundational|intermediate|advanced.\n\n"
        f"Return JSON in EXACTLY this shape, omitting any empty axis:\n{_AXES_SHAPE}"
    )


def _normalize_axes(raw) -> dict:
    """Coerce the LLM `axes` object to the stored shape: known axes only, capped lists,
    typed sub-fields, empties dropped. Idempotent (safe to re-run on stored data)."""
    if not isinstance(raw, dict):
        return {}

    def _s(v, n=240):
        return str(v).strip()[:n] if v not in (None, "") else ""

    def _slist(v, cap=12):
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return []
        return [_s(x, 200) for x in v if _s(x)][:cap]

    out: dict = {}
    for axis, fields in AXIS_SPEC.items():
        v = raw.get(axis)
        if axis == "questions":
            q = _slist(v, _AXIS_CAP)
            if q:
                out["questions"] = q
            continue
        if not isinstance(v, list):
            continue
        recs = []
        for item in v[:_AXIS_CAP]:
            if not isinstance(item, dict):
                continue
            rec: dict = {}
            for f in fields:
                if f in _LIST_SUBFIELDS:
                    lv = _slist(item.get(f))
                    if lv:
                        rec[f] = lv
                elif f == "cardinality":
                    try:
                        rec[f] = int(item.get(f))
                    except (TypeError, ValueError):
                        pass
                elif f == "polarity":
                    p = _s(item.get(f)).lower()
                    if p in _POLARITIES:
                        rec[f] = p
                else:
                    sv = _s(item.get(f))
                    if sv:
                        rec[f] = sv
            if fields and rec.get(fields[0]):
                recs.append(rec)
        if recs:
            out[axis] = recs

    diff = raw.get("difficulty")
    if isinstance(diff, dict):
        d: dict = {}
        lvl = _s(diff.get("level")).lower()
        if lvl in _LEVELS:
            d["level"] = lvl
        pre = _slist(diff.get("prerequisites"))
        if pre:
            d["prerequisites"] = pre
        if d:
            out["difficulty"] = d
    return out


def _load_prior_axes(vault):
    """Prior axes + content hashes for idempotent re-runs."""
    prior_hash: dict[str, str] = {}
    prior_axes: dict[str, dict] = {}
    try:
        a = json.loads(vault.enrichment_axes_json.read_text(encoding="utf-8"))
        prior_hash = dict(a.get("hashes") or {})
        prior_axes = dict(a.get("pages") or {})
    except (OSError, json.JSONDecodeError):
        pass
    return prior_hash, prior_axes


def extract_axes_llm(
    vault: VaultPaths,
    result: EnrichmentResult,
    *,
    client,
    model: str = "claude-haiku-4-5",
    cost_tracker,
    top_frac: float = 0.2,
    top_n: int | None = None,
    max_targets: int = 24,
    progress=None,
) -> dict:
    """Phase B (unified): one LLM call per selected page lifts typed edges, atomic
    propositions, AND the domain-neutral semantic axes (stance, viewpoints, analogies,
    symbols, procedures, stages, parts, functions, caveats, quantities, places,
    definitions, questions, difficulty). Selects the top-N nodes by centrality across
    ALL pages (axes apply to every page, not only linked ones). Content-hash-gated
    against the sidecars on disk and budget-capped (rest stay unenriched)."""
    prior_hash, prior_edges, prior_props = _load_prior_typing(vault)
    _ax_hash, ax_prior = _load_prior_axes(vault)

    cand = sorted(result.nodes.items(), key=lambda kv: -kv[1].centrality)
    k = top_n if top_n is not None else max(1, math.ceil(top_frac * max(1, len(result.nodes))))
    cand = cand[:k]

    stats = {"selected": len(cand), "llm_calls": 0, "reused": 0, "typed_edges": 0,
             "props": 0, "axes_pages": 0, "axis_records": 0, "failed": 0,
             "stopped_budget": False}

    for pid, node in cand:
        unchanged = node.content_hash != "" and prior_hash.get(pid) == node.content_hash
        # Short-circuit the LLM only when this page's axes are already cached. Pages
        # that were edge-typed by an earlier run (but never axis-extracted) still call.
        if unchanged and pid in ax_prior:
            te = prior_edges.get(pid, {})
            for e in node.edges:
                if e.type is None and e.target in te:
                    pe = te[e.target]
                    e.type, e.via = pe.get("type"), "llm"
                    e.conf, e.evidence = pe.get("conf"), pe.get("evidence", "")
                    stats["typed_edges"] += 1
            for p in prior_props.get(pid, []):
                result.claims.setdefault(pid, []).append(
                    Claim(text=p.get("text", ""), kind="proposition",
                          provenance=p.get("provenance", []),
                          salience=p.get("salience"), via="llm"))
                stats["props"] += 1
            axd = _normalize_axes(ax_prior.get(pid, {}))
            if axd:
                result.axes[pid] = PageAxes(content_hash=node.content_hash, data=axd)
                stats["axes_pages"] += 1
                stats["axis_records"] += result.axes[pid].count
            stats["reused"] += 1
            continue

        try:
            cost_tracker.check_budget()
        except Exception:
            stats["stopped_budget"] = True
            break

        page = read_page(vault, pid)
        if page is None:
            continue
        targets = [e.target for e in node.edges if e.type is None][:max_targets]
        tinfo = []
        for t in targets:
            tp = read_page(vault, t)
            tinfo.append({"id": t, "title": tp.title if tp else t,
                          "summary": (tp.summary if tp else "")[:160]})
        if progress:
            progress(pid, len(tinfo))
        try:
            resp = client.messages.create(
                model=model, max_tokens=4000, system=_AXES_SYSTEM,
                messages=[{"role": "user", "content": _build_extract_prompt(page, tinfo)}])
            cost_tracker.record_call(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                model=model, is_sub_call=True)
            data = _parse_json(resp.content[0].text)
        except Exception:
            stats["failed"] += 1
            continue
        stats["llm_calls"] += 1

        by_target = {e.target: e for e in node.edges if e.type is None}
        for ed in (data.get("edges") or []):
            e = by_target.get(ed.get("target"))
            if e is None:
                continue
            typ = ed.get("type") or "related"
            if typ not in PREDICATES and typ != "related":
                typ = "related"
            e.type, e.via = typ, "llm"
            try:
                e.conf = float(ed.get("conf"))
            except (TypeError, ValueError):
                e.conf = 0.7
            e.evidence = str(ed.get("evidence") or "")[:160]
            stats["typed_edges"] += 1
        for cl in (data.get("claims") or [])[:6]:
            txt = str(cl.get("text") or "").strip()
            if not txt:
                continue
            try:
                sal = float(cl.get("salience"))
            except (TypeError, ValueError):
                sal = None
            result.claims.setdefault(pid, []).append(
                Claim(text=txt, kind="proposition", provenance=list(page.sources),
                      salience=sal, via="llm"))
            stats["props"] += 1
        axd = _normalize_axes(data.get("axes") or {})
        if axd:
            result.axes[pid] = PageAxes(content_hash=node.content_hash, data=axd)
            stats["axes_pages"] += 1
            stats["axis_records"] += result.axes[pid].count

    result.axes_extracted = True
    result.method = "hybrid"
    return stats


# ---------------------------------------------------------------------------
# Sidecar writers (atomic — mirror contradiction_ledger.save)
# ---------------------------------------------------------------------------
def _atomic_write_json(path, meta_dir, data: dict, prefix: str) -> None:
    meta_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".json.tmp", dir=str(meta_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_enrichment(vault: VaultPaths, result: EnrichmentResult) -> None:
    """Write the enrichment sidecars + the human report."""
    base = {"version": _SCHEMA_VERSION, "timestamp": result.timestamp,
            "topic": result.topic, "method": result.method}

    _atomic_write_json(
        vault.enrichment_graph_json, vault.meta,
        {**base, "nodes": {pid: n.to_dict() for pid, n in sorted(result.nodes.items())}},
        ".enrichment-graph.")

    _atomic_write_json(
        vault.enrichment_temporal_json, vault.meta,
        {**base, "events": [t.to_dict() for t in result.temporal]},
        ".enrichment-temporal.")

    _atomic_write_json(
        vault.enrichment_claims_json, vault.meta,
        {**base, "grounded": result.claims_grounded,
         "pages": {pid: [c.to_dict() for c in cs] for pid, cs in sorted(result.claims.items())}},
        ".enrichment-claims.")

    _atomic_write_json(
        vault.enrichment_terms_json, vault.meta,
        {**base, "terms": {pid: t.to_dict() for pid, t in sorted(result.terms.items())}},
        ".enrichment-terms.")

    if result.axes:
        _atomic_write_json(
            vault.enrichment_axes_json, vault.meta,
            {**base,
             "pages": {pid: a.to_dict() for pid, a in sorted(result.axes.items())},
             "hashes": {pid: a.content_hash for pid, a in sorted(result.axes.items())}},
            ".enrichment-axes.")

    vault.enrichment_md.write_text(render_enrichment_md(result), encoding="utf-8")


def merge_axes_staging(vault: VaultPaths, *, clear: bool = True) -> dict:
    """Fold per-page axes staged by batch ingest subagents into enrichment-axes.json.

    Convention: each batch subagent writes ONE file `wiki/_meta/axes-staging/<stem>.json`
    mapping page-id -> raw axes (the AXIS_SPEC shape). We normalize each via
    `_normalize_axes`, stamp the page's current content_hash, and MERGE into the existing
    sidecar — so subagent-staged axes and `enrich --mode hybrid` axes share one file and
    one cache. This is how FORWARD ingests carry axes without the API enrich pass."""
    staging = vault.meta / "axes-staging"
    stats = {"files": 0, "pages": 0, "records": 0, "skipped_missing": 0}
    if not staging.is_dir():
        return stats

    sidecar = {"version": _SCHEMA_VERSION, "timestamp": today_iso(),
               "topic": _read_topic(vault), "method": "hybrid", "pages": {}, "hashes": {}}
    try:
        prev = json.loads(vault.enrichment_axes_json.read_text(encoding="utf-8"))
        if isinstance(prev, dict):
            sidecar["pages"] = dict(prev.get("pages") or {})
            sidecar["hashes"] = dict(prev.get("hashes") or {})
    except (OSError, json.JSONDecodeError):
        pass

    files = sorted(staging.glob("*.json"))
    for fp in files:
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        stats["files"] += 1
        for pid, raw in obj.items():
            page = read_page(vault, pid)
            if page is None:
                stats["skipped_missing"] += 1
                continue
            axd = _normalize_axes(raw)
            if not axd:
                continue
            sidecar["pages"][pid] = axd
            sidecar["hashes"][pid] = _content_hash(page.title, page.body)
            stats["pages"] += 1
            stats["records"] += sum(len(v) if isinstance(v, list) else 1 for v in axd.values())

    sidecar["timestamp"] = today_iso()
    sidecar["pages"] = dict(sorted(sidecar["pages"].items()))
    sidecar["hashes"] = dict(sorted(sidecar["hashes"].items()))
    _atomic_write_json(vault.enrichment_axes_json, vault.meta, sidecar, ".enrichment-axes.")

    if clear:
        for fp in files:
            try:
                fp.unlink()
            except OSError:
                pass
        try:
            staging.rmdir()
        except OSError:
            pass
    return stats


def render_enrichment_md(r: EnrichmentResult) -> str:
    top = sorted(r.nodes.items(), key=lambda kv: -kv[1].centrality)[:12]
    dated = [t for t in r.temporal if t.year is not None]
    lines = [
        f"# Enrichment — {r.topic}",
        "",
        f"*updated {r.timestamp} · method: {r.method}*",
        "",
        f"- **{r.page_count}** nodes · **{r.edge_count}** edges "
        f"({r.typed_edge_count} typed) · **{len(r.temporal)}** temporal anchors "
        f"· **{len(r.terms)}** terms · **{r.claim_count}** claims"
        f"{'' if r.claims_grounded else ' (grounding skipped)'}",
        "",
        "## Most central nodes (salience)",
    ]
    for pid, n in top:
        lines.append(f"- `{pid}` — in:{n.in_degree} out:{n.out_degree} "
                     f"centrality {n.centrality:.2f}")
    if dated:
        lines += ["", "## Timeline (earliest → latest)"]
        for t in dated[:24]:
            lines.append(f"- **{t.text}** ({t.year}) — `{t.page}`")
    if r.axes:
        from collections import Counter
        tally: Counter = Counter()
        for a in r.axes.values():
            for axis, v in a.data.items():
                tally[axis] += len(v) if isinstance(v, list) else 1
        lines += ["", "## Semantic axes (Phase B — LLM)",
                  f"- **{r.axis_count}** records across **{len(r.axes)}** pages",
                  "- " + " · ".join(f"{k}:{n}" for k, n in tally.most_common())]
    if r.method != "hybrid":
        lines += ["", "## Next: Phase B (bounded LLM) types edges, lifts propositions, and "
                  "extracts semantic axes on high-salience nodes — run `enrich --mode hybrid`.", ""]
    return "\n".join(lines).rstrip() + "\n"
