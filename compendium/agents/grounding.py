"""Claim grounding — verify quotes on pages against the cited raw source.

The Linter's citation check answers "is this citation a real, findable
work?" It does NOT answer the question that actually protects a vault from
hallucination: **does the quoted text on a page actually appear in the
source it cites?** A fan-out subagent can invent a plausible quotation or
misattribute a real one, and nothing downstream would ever catch it.

This module closes that gap, mechanically and for free (no LLM):

1. Extract quoted strings from each wiki page body.
2. Resolve the page's cited `sources:` to raw files in `raw/`
   (exact stem → prefix → token-overlap; PDFs are text-extracted via
   PyMuPDF and cached alongside as `<stem>.extracted.txt`).
3. Normalize both sides — strip transcript timestamps, markdown, smart
   quotes, and (for a punctuation-insensitive pass) all non-alphanumerics,
   which absorbs OCR hyphenation and whitespace drift.
4. Match each quote: exact / compact substring → **grounded**; anchored
   fuzzy ratio → **grounded** (≥0.85) / **loose** (0.6–0.85, likely a
   paraphrase or OCR noise) / **not-found** (<0.6, possible fabrication).

Quotes whose source has no resolvable raw text are reported as
**unverifiable** (honest: we can't check), not as failures.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from compendium.models import PageType
from compendium.vault import VaultPaths, list_pages, read_page, today_iso

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

GROUNDED_THRESHOLD = 0.85
# Anchor-aligned ratio. Edited/elided real quotes and OCR-degraded PDF text
# cluster in the 0.45–0.85 band; treat those as "loose" (present but
# imperfect) so "not-found" is reserved for quotes with little real overlap
# with the cited source — the genuine misattribution/fabrication signal.
LOOSE_THRESHOLD = 0.45
SOURCE_MATCH_FLOOR = 0.50          # min token-coverage to trust a fuzzy file map
MIN_QUOTE_CHARS = 28
MIN_QUOTE_WORDS = 5
MAX_QUOTE_CHARS = 600
LONG_QUOTE_WORDS = 11             # at/above this, a not-found is high-priority

# Tokens that carry no identifying signal when matching a source-id to a
# raw filename.
_FILENAME_STOP = frozenset({
    "yt", "the", "a", "an", "of", "and", "to", "for", "on", "in", "by",
    "with", "vol", "volume", "pdf", "txt", "md", "com", "www", "http",
    "https", "ok", "xyz", "extracts", "extract", "transcript", "draw",
    "aside", "veil", "attempt",
})

_ELLIPSIS_RE = re.compile(r"\[?\s*(?:\.\.\.|…)\s*\]?")
_TIMESTAMP_RE = re.compile(r"\[\s*\d{1,2}:\d{2}(?::\d{2})?\s*\]")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SOURCES_HEADING_RE = re.compile(
    r"^##+\s+(?:Sources?|References?|Bibliography|Works\s+Cited|Citations?|"
    r"Further\s+Reading|See\s+Also)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Quote shapes. Double quotes (straight + curly) and long single/curly
# single quotes. Single quotes require non-letter boundaries to dodge
# contractions ("Pythagoras's"); length filtering does the rest.
_DOUBLE_QUOTE_RE = re.compile(r"[\"“]([^\"“”\n]{12,600})[\"”]")
_SINGLE_QUOTE_RE = re.compile(
    r"(?<![A-Za-z0-9])[\'‘]([^\'‘’\n]{24,600})[\'’](?![A-Za-z0-9])"
)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class QuoteCheck:
    page_id: str
    quote: str
    status: str               # grounded | loose | not-found | unverifiable
    score: float = 0.0
    source_matched: str = ""  # source_id whose raw text best matched
    method: str = ""          # exact | compact | fuzzy | no-source-text
    note: str = ""


@dataclass
class GroundingReport:
    timestamp: str = ""
    topic: str = ""
    pages_checked: int = 0
    quotes_total: int = 0
    grounded: int = 0
    loose: int = 0
    not_found: int = 0
    unverifiable: int = 0
    checks: list[QuoteCheck] = field(default_factory=list)
    unmapped_sources: list[str] = field(default_factory=list)  # source_id, reason
    pdf_skipped: bool = False


# ---------------------------------------------------------------------------
# Raw-source index + resolution
# ---------------------------------------------------------------------------


@dataclass
class _Haystack:
    source_id: str
    path: Path
    score: float
    method: str
    norm: str = ""
    compact: str = ""
    available: bool = True
    reason: str = ""


def _tok(text: str) -> set[str]:
    raw = re.split(r"[^a-z0-9]+", text.lower())
    return {t for t in raw if len(t) >= 2 and t not in _FILENAME_STOP}


def _strip_dedup_suffix(sid: str) -> str:
    """`yt-foo-foo-2` → `yt-foo-foo` (drop a trailing `-<n>` dedup tag)."""
    return re.sub(r"-\d+$", "", sid)


class _RawIndex:
    """Maps a source_id to the best-matching raw file and its text."""

    def __init__(self, vault: VaultPaths):
        self.vault = vault
        self._files: list[tuple[str, Path]] = []  # (stem, path)
        self._cache: dict[str, _Haystack | None] = {}
        self.pdf_skipped = False
        for d in (vault.raw_articles, vault.raw_papers, vault.raw_transcripts):
            if not d.is_dir():
                continue
            for p in sorted(d.iterdir()):
                if not p.is_file():
                    continue
                if p.suffix.lower() in (".md", ".txt", ".pdf"):
                    # Skip our own extracted-text caches as primary candidates;
                    # they're picked up implicitly when the PDF resolves.
                    if p.name.endswith(".extracted.txt"):
                        continue
                    self._files.append((p.stem, p))

    # -- resolution --------------------------------------------------------

    def resolve(self, source_id: str) -> _Haystack | None:
        if source_id in self._cache:
            return self._cache[source_id]
        hay = self._resolve_uncached(source_id)
        self._cache[source_id] = hay
        return hay

    def _resolve_uncached(self, source_id: str) -> _Haystack | None:
        sid = source_id.lower().strip()
        base = _strip_dedup_suffix(sid)
        sid_tokens = _tok(sid)

        best: tuple[float, str, Path] | None = None  # (score, method, path)
        for stem, path in self._files:
            stem_l = stem.lower()
            if stem_l == sid or stem_l == base:
                best = (1.0, "exact", path)
                break
            score, method = 0.0, ""
            if (
                sid.startswith(stem_l)
                or stem_l.startswith(base)
                or base.startswith(stem_l)
            ):
                score, method = 0.92, "prefix"
            else:
                stem_tokens = _tok(stem_l)
                if sid_tokens and stem_tokens:
                    overlap = len(sid_tokens & stem_tokens)
                    cov = overlap / len(sid_tokens)
                    if cov > score:
                        score, method = cov, "token-overlap"
            if score > 0 and (best is None or score > best[0]):
                best = (score, method, path)

        if best is None or best[0] < SOURCE_MATCH_FLOOR:
            return None

        score, method, path = best
        norm, compact, available, reason = self._load_text(path)
        return _Haystack(
            source_id=source_id,
            path=path,
            score=score,
            method=method,
            norm=norm,
            compact=compact,
            available=available,
            reason=reason,
        )

    # -- text loading ------------------------------------------------------

    def _load_text(self, path: Path) -> tuple[str, str, bool, str]:
        if path.suffix.lower() == ".pdf":
            text, ok, reason = self._pdf_text(path)
            if not ok:
                return "", "", False, reason
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return "", "", False, f"read failed: {exc}"
        norm = _normalize(text)
        return norm, _compact(norm), True, ""

    def _pdf_text(self, path: Path) -> tuple[str, bool, str]:
        cache = path.with_suffix(path.suffix + ".extracted.txt")
        # Reuse a fresh cache.
        try:
            if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
                return cache.read_text(encoding="utf-8", errors="replace"), True, ""
        except OSError:
            pass
        try:
            import fitz  # PyMuPDF
        except Exception:
            self.pdf_skipped = True
            return "", False, "PyMuPDF not available — PDF not extracted"
        try:
            parts: list[str] = []
            with fitz.open(path) as doc:
                for page in doc:
                    parts.append(page.get_text("text"))
            text = "\n".join(parts)
        except Exception as exc:
            return "", False, f"PDF extraction failed: {exc}"
        try:
            cache.write_text(text, encoding="utf-8", newline="\n")
        except OSError:
            pass
        return text, True, ""


# ---------------------------------------------------------------------------
# Normalization + matching
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _TIMESTAMP_RE.sub(" ", text)
    text = (
        text.replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
        .replace("—", "-").replace("–", "-")
        .replace(" ", " ")
    )
    text = re.sub(r"[*_`>#]+", " ", text)
    text = text.lower()
    return _WS_RE.sub(" ", text).strip()


def _compact(norm_text: str) -> str:
    """Punctuation/whitespace-insensitive form (absorbs OCR hyphenation)."""
    return _NON_ALNUM_RE.sub("", norm_text)


def _fuzzy_best(q_norm: str, hay_norm: str) -> float:
    """Best fuzzy ratio of the quote against an anchor-aligned window.

    The window is sized to the quote's length and *aligned* so the anchor
    word lands at the same offset it occupies in the quote. A naive window
    of 2·len(quote) would dilute the ratio with surrounding text — a
    perfect contiguous match would top out near 0.67 and be misjudged.
    """
    Lq = len(q_norm)
    if Lq == 0 or not hay_norm:
        return 0.0
    # Anchor on clean alphanumeric tokens — NOT q_norm.split(), which leaves
    # trailing punctuation ("harmony.", "beautiful?") that never matches the
    # haystack and silently zeroes the score.
    tokens = re.findall(r"[a-z0-9]+", q_norm)
    anchors = sorted({w for w in tokens if len(w) >= 6}, key=len, reverse=True)
    if not anchors:
        anchors = [w for w in tokens if len(w) >= 4][:4]
    if not anchors:
        return 0.0
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(q_norm)
    best = 0.0
    for w in anchors[:4]:
        qi = q_norm.find(w)
        if qi < 0:
            continue
        start, hits = 0, 0
        while hits < 8:
            p = hay_norm.find(w, start)
            if p < 0:
                break
            hits += 1
            astart = max(0, p - qi)
            window = hay_norm[astart : astart + Lq]
            sm.set_seq1(window)
            if sm.real_quick_ratio() > best and sm.quick_ratio() > best:
                r = sm.ratio()
                if r > best:
                    best = r
            start = p + len(w)
            if best >= 0.995:
                return best
    return best


def _match_quote(q_norm: str, q_compact: str, hays: list[_Haystack]):
    """Return (status, score, source_id, method) for one quote."""
    best_status, best_score, best_src, best_method = "not-found", 0.0, "", "fuzzy"
    for h in hays:
        if not h.available or not h.norm:
            continue
        if q_norm and q_norm in h.norm:
            return "grounded", 1.0, h.source_id, "exact"
        if q_compact and len(q_compact) >= 16 and q_compact in h.compact:
            return "grounded", 0.99, h.source_id, "compact"
        score = _fuzzy_best(q_norm, h.norm)
        if score > best_score:
            best_score, best_src = score, h.source_id
    if best_score >= GROUNDED_THRESHOLD:
        best_status = "grounded"
    elif best_score >= LOOSE_THRESHOLD:
        best_status = "loose"
    return best_status, best_score, best_src, best_method


def _ground_one(raw_quote: str, hays: list[_Haystack]):
    """Ground one quote, accounting for academic elision ("A ... B").

    A whole-quote match is tried first. If that fails and the quote is an
    elided composite, each substantial fragment is checked independently —
    an edited quote whose pieces are all present is genuinely grounded, not
    a fabrication.
    """
    q_norm = _normalize(raw_quote)
    q_comp = _compact(q_norm)
    status, score, src, method = _match_quote(q_norm, q_comp, hays)
    if status == "grounded":
        return status, score, src, method

    parts = [p.strip() for p in _ELLIPSIS_RE.split(raw_quote)]
    frags = [p for p in parts if len(p) >= 16 and len(p.split()) >= 3]
    if len(frags) >= 2:
        sub = [
            _match_quote(_normalize(f), _compact(_normalize(f)), hays)
            for f in frags
        ]
        statuses = [s[0] for s in sub]
        if all(s == "grounded" for s in statuses):
            return "grounded", min(s[1] for s in sub), sub[0][2], "fragments"
        if all(s in ("grounded", "loose") for s in statuses):
            return "loose", min(s[1] for s in sub), sub[0][2], "fragments"

    return status, score, src, method


# ---------------------------------------------------------------------------
# Quote extraction
# ---------------------------------------------------------------------------


def _strip_back_matter(body: str) -> str:
    """Cut a trailing Sources/References/See-Also section off the body."""
    matches = list(_SOURCES_HEADING_RE.finditer(body))
    if not matches:
        return body
    return body[: matches[-1].start()]


def _extract_quotes(body: str) -> list[str]:
    text = _strip_back_matter(body)
    seen: set[str] = set()
    out: list[str] = []
    for rx in (_DOUBLE_QUOTE_RE, _SINGLE_QUOTE_RE):
        for m in rx.finditer(text):
            q = m.group(1).strip()
            if "[[" in q or "](" in q:  # wikilink / markdown link, not a quote
                continue
            if "**" in q:  # markdown bold = author emphasis, not a clean quote
                continue
            if q[:1] in ").,;:]}>":  # mid-sentence / broken span, not a quote
                continue
            if len(q) < MIN_QUOTE_CHARS or len(q) > MAX_QUOTE_CHARS:
                continue
            if len(q.split()) < MIN_QUOTE_WORDS:
                continue
            key = _compact(_normalize(q))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(q)
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ground_vault(vault: VaultPaths) -> GroundingReport:
    """Check every quoted passage against its page's cited raw sources."""
    index = _RawIndex(vault)
    report = GroundingReport(timestamp=today_iso(), topic=_read_topic(vault))
    unmapped: dict[str, str] = {}

    for page_id in list_pages(vault):
        page = read_page(vault, page_id)
        if page is None or not page.sources:
            continue
        quotes = _extract_quotes(page.body)
        if not quotes:
            continue

        hays: list[_Haystack] = []
        for sid in page.sources:
            hay = index.resolve(sid)
            if hay is None:
                unmapped.setdefault(sid, "no matching raw file")
            elif not hay.available:
                unmapped.setdefault(sid, hay.reason or "raw text unavailable")
                hays.append(hay)  # keep for transparency; norm is empty
            else:
                hays.append(hay)

        usable = [h for h in hays if h.available and h.norm]
        report.pages_checked += 1
        for q in quotes:
            report.quotes_total += 1
            if not usable:
                report.unverifiable += 1
                report.checks.append(QuoteCheck(
                    page_id=page_id, quote=q, status="unverifiable",
                    method="no-source-text",
                    note="cited source(s) have no resolvable raw text",
                ))
                continue
            status, score, src, method = _ground_one(q, usable)
            if status == "grounded":
                report.grounded += 1
            elif status == "loose":
                report.loose += 1
            else:
                report.not_found += 1
            report.checks.append(QuoteCheck(
                page_id=page_id, quote=q, status=status, score=round(score, 3),
                source_matched=src, method=method,
            ))

    report.unmapped_sources = [f"{sid} — {why}" for sid, why in sorted(unmapped.items())]
    report.pdf_skipped = index.pdf_skipped
    return report


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_grounding_md(report: GroundingReport) -> str:
    lines = ["# Claim Grounding", ""]
    header = (
        f"updated {report.timestamp} · {report.quotes_total} quotes across "
        f"{report.pages_checked} pages"
    )
    if report.topic:
        header = f"{report.topic} · " + header
    lines.append(f"*{header}*")
    lines.append("")
    lines.append(
        f"- **{report.grounded}** grounded · "
        f"**{report.loose}** loose (paraphrase/OCR) · "
        f"**{report.not_found}** NOT FOUND · "
        f"**{report.unverifiable}** unverifiable"
    )
    lines.append("")
    lines.append(
        "> Quoted passages checked verbatim against the raw text of each "
        "page's cited source(s). A **not-found** quote is *not in the source "
        "the page cites* — most often **misattribution** (a real quote from "
        "some other work, cited gesturally to a lecture/overview that merely "
        "discusses it) and occasionally **fabrication**. Triage long passages "
        "with low scores first; short quoted phrases are usually work-titles, "
        "maxims, or glosses and are low-stakes. **loose** = close-but-"
        "imperfect (paraphrase-as-quote or OCR drift). **unverifiable** = the "
        "cited source has no machine-readable raw text in `raw/`."
    )
    lines.append("")

    not_found = [c for c in report.checks if c.status == "not-found"]
    loose = [c for c in report.checks if c.status == "loose"]
    long_nf = sorted(
        [c for c in not_found if len(c.quote.split()) >= LONG_QUOTE_WORDS],
        key=lambda c: -len(c.quote.split()),
    )
    short_nf = [c for c in not_found if len(c.quote.split()) < LONG_QUOTE_WORDS]

    def _by_page(checks: list[QuoteCheck]) -> dict[str, list[QuoteCheck]]:
        grouped: dict[str, list[QuoteCheck]] = {}
        for c in checks:
            grouped.setdefault(c.page_id, []).append(c)
        return grouped

    if not not_found:
        lines.append(
            "_Every quote was located in a cited source — no misattribution "
            "or fabrication signals._"
        )
        lines.append("")
    if long_nf:
        lines.append(
            f"## ❌ High-priority: {len(long_nf)} long passage(s) not in cited source"
        )
        lines.append("")
        lines.append(
            "> Long verbatim-style quotes absent from the source they cite. "
            "Verify each: either fix the citation (the quote belongs to a "
            "different work) or remove/flag it if fabricated."
        )
        lines.append("")
        for c in long_nf:
            lines.append(f"- [[{c.page_id}]] — \"{_clip(c.quote)}\"")
            lines.append(
                f"    - best match {c.score:.0%}"
                + (f" in `{c.source_matched}`" if c.source_matched else " (no candidate)")
            )
        lines.append("")
    if short_nf:
        lines.append(
            f"<details><summary>Short not-found quotes "
            f"({len(short_nf)}) — usually titles / maxims / glosses</summary>"
        )
        lines.append("")
        for pid, items in _by_page(short_nf).items():
            lines.append(f"**[[{pid}]]**")
            for c in items:
                lines.append(
                    f"- \"{_clip(c.quote)}\" — {c.score:.0%}"
                    + (f" `{c.source_matched}`" if c.source_matched else "")
                )
            lines.append("")
        lines.append("</details>")
        lines.append("")

    if loose:
        lines.append(
            f"<details><summary>⚠️ Loose matches ({len(loose)}) — "
            f"paraphrase-as-quote or OCR drift</summary>"
        )
        lines.append("")
        for pid, items in _by_page(loose).items():
            lines.append(f"**[[{pid}]]**")
            for c in items:
                lines.append(
                    f"- \"{_clip(c.quote)}\" — {c.score:.0%}"
                    + (f" `{c.source_matched}`" if c.source_matched else "")
                )
            lines.append("")
        lines.append("</details>")
        lines.append("")

    if report.unmapped_sources:
        lines.append("## Sources with no verifiable raw text")
        lines.append("")
        lines.append(
            "> Quotes citing these sources are reported as *unverifiable*. "
            "Extract the source text into `raw/` (PDF → `.md`/`.txt`) to "
            "enable grounding."
        )
        for s in report.unmapped_sources:
            lines.append(f"- {s}")
        lines.append("")
        if report.pdf_skipped:
            lines.append(
                "_Note: PyMuPDF was unavailable, so PDF sources could not be "
                "text-extracted this run._"
            )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _clip(text: str, n: int = 160) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _read_topic(vault: VaultPaths) -> str:
    if not vault.claude_md.exists():
        return ""
    try:
        text = vault.claude_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("# Vault Schema"):
            _, _, after = line.partition("—")
            return after.strip()
    return ""
