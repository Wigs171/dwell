"""
Citation verification via multi-strategy web search.

For each citation in an entry, parses author/title/year/publisher/type,
then tries up to 3 search strategies (relaxed keywords, title-focused,
author+context) with word-overlap scoring instead of exact substring matching.

Inspired by the observation that exact-quoted searches fail for 55%+ of
scholarly citations (ancient texts, academic books, non-English titles)
even when the citations are canonical and accurate.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Callable

from compendium.models import VerificationResult

# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------

TITLE_STOP_WORDS = frozenset({
    "the", "a", "an", "of", "and", "in", "to", "for", "on", "by",
    "with", "from", "its", "their", "this", "that", "as", "or", "is",
    "was", "are", "at", "be", "it", "not", "but", "has", "had", "have",
    "his", "her", "he", "she", "they", "we", "our", "new", "vol",
})

KNOWN_REPOSITORIES = frozenset({
    "british museum", "british library", "nag hammadi", "bodleian",
    "vatican library", "bibliotheca", "bibliotheque nationale",
    "papyrus", "codex", "manuscript", "dead sea scrolls",
    "national library", "rijksmuseum", "louvre", "metropolitan museum",
    "saqqara", "pyramid texts", "coffin texts",
})

# Matches common academic publisher names
PUBLISHER_RE = re.compile(
    r"(?:University\s+Press|Cambridge|Oxford|Harvard|Princeton|Yale|"
    r"Columbia|Stanford|Cornell|MIT\s+Press|Chicago|Duke|"
    r"Routledge|Springer|Wiley|Brill|de\s+Gruyter|Palgrave|"
    r"Penguin|Random\s+House|Viking|Pantheon|Norton|"
    r"Loeb\s+Classical\s+Library|Les\s+Belles\s+Lettres|"
    r"Warburg\s+Institute|Clarendon|Athlone|Beacon|"
    r"Harper|Simon|Bantam|Vintage|Dover|Academic\s+Press)",
    re.IGNORECASE,
)

# Patterns indicating a translator or editor (not primary author)
TRANS_ED_RE = re.compile(
    r"\btrans(?:lated)?\.?\s+(?:by\s+)?|"
    r"\bed(?:ited)?\.?\s+(?:by\s+)?|"
    r"\beds?\.\s+",
    re.IGNORECASE,
)

# Matches arxiv IDs in "NNNN.NNNNN" form (YYMM.sequence, post-2015 convention).
# `(?:arxiv:)?` optional prefix; we only catch 4-digit YYMM then dot then 4-6 digits.
_ARXIV_ID_RE = re.compile(
    r"\b(?:arxiv:?|arXiv:?)?\s*(\d{4})\.(\d{4,6})\b", re.IGNORECASE,
)

# A 4-digit year 1900-2099 in the citation body.
_CITATION_YEAR_RE = re.compile(r"\b(1\d{3}|20\d{2})\b")


def detect_temporal_impossibility(
    citation: str, *, as_of: date | None = None
) -> str | None:
    """Spot citations with dates that can't possibly exist yet.

    Catches two failure modes:
    - Fabricated arxiv IDs like `2601.15286` (January 2026) cited by
      a 2023-era source — the archive paper couldn't have existed when
      the source was written.
    - Generic 4-digit years in the future.

    Returns a one-line reason string on detection, None when the
    citation is temporally plausible. Fast, mechanical, no LLM.
    """
    today = as_of or date.today()
    cy, cm = today.year, today.month

    for m in _ARXIV_ID_RE.finditer(citation):
        yymm = m.group(1)
        try:
            yy = int(yymm[:2])
            mm = int(yymm[2:4])
        except ValueError:
            continue
        if mm < 1 or mm > 12:
            continue
        # Post-2015 arxiv convention: YYMM prefix with YY in 15-99 → 2015-2099
        if yy < 15:
            continue  # pre-2015 format, ambiguous — skip
        aid_year = 2000 + yy
        if (aid_year, mm) > (cy, cm):
            return (
                f"arxiv id {m.group(0).strip()} dates to "
                f"{aid_year}-{mm:02d}, which is after today "
                f"({cy}-{cm:02d})"
            )

    # Generic future-year detection
    for m in _CITATION_YEAR_RE.finditer(citation):
        year = int(m.group(1))
        if year > cy:
            return f"citation year {year} is in the future (today is {cy})"

    return None


def _normalize_for_search(text: str) -> str:
    """Strip diacritics for search queries.  Festugière → Festugiere."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _tokenize_significant(text: str) -> set[str]:
    """Lowercase, split on non-alpha, remove stop words.  Returns set of meaningful words."""
    words = re.findall(r"[a-z]{2,}", text.lower())
    return {w for w in words if w not in TITLE_STOP_WORDS}


def _extract_last_name(name: str) -> str:
    """Extract a single last name from an author string.

    'Copenhaver, Brian P.' → 'Copenhaver'
    'Brian P. Copenhaver'  → 'Copenhaver'
    'Plotinus'             → 'Plotinus'
    """
    name = name.strip().rstrip(".")
    if not name:
        return ""
    # "Last, First" format
    if "," in name:
        return name.split(",")[0].strip()
    # "First Last" format — take the last word
    parts = name.split()
    # Skip trailing abbreviations like "Jr." or "III"
    for p in reversed(parts):
        cleaned = p.strip(".")
        if len(cleaned) > 2 and not cleaned.isdigit():
            return cleaned
    return parts[-1] if parts else ""


def _extract_all_last_names(citation: str, author_segment: str) -> list[str]:
    """Extract all author last names, handling multi-author formats.

    Splits on ' and ', '; ', ' & ' and extracts last name from each.
    Filters out translator/editor names.
    """
    # Remove translator/editor segments first
    cleaned = TRANS_ED_RE.split(author_segment)[0].strip()
    if not cleaned:
        return []

    # Split multi-author strings
    authors = re.split(r"\s+and\s+|\s*;\s+|\s*&\s+", cleaned)
    last_names = []
    for a in authors:
        a = a.strip().rstrip(",").strip()
        if not a or len(a) < 2:
            continue
        ln = _extract_last_name(a)
        if ln and len(ln) > 1 and not ln.isdigit():
            last_names.append(ln)
    return last_names


# ---------------------------------------------------------------------------
# Citation type classification
# ---------------------------------------------------------------------------

def _classify_citation(citation: str, year: str) -> str:
    """Classify a citation as book/paper/ancient/manuscript/unknown."""
    cl = citation.lower()

    # Manuscript / primary source
    if any(repo in cl for repo in KNOWN_REPOSITORIES):
        return "manuscript"

    # Ancient text
    if year:
        try:
            yr = int(year)
            if yr < 1400:
                return "ancient"
        except ValueError:
            pass
    if re.search(r"\b(?:BCE|CE|B\.C\.|A\.D\.)\b", citation, re.IGNORECASE):
        return "ancient"

    # Journal article
    if re.search(
        r"\b(?:Journal|Review|Proceedings|Quarterly|Bulletin|Annals)\b",
        citation, re.IGNORECASE,
    ) or re.search(r"\bvol(?:ume)?\.?\s*\d", citation, re.IGNORECASE):
        return "paper"

    # Book (publisher found)
    if PUBLISHER_RE.search(citation):
        return "book"

    return "unknown"


# ---------------------------------------------------------------------------
# SourceVerifier class
# ---------------------------------------------------------------------------

class SourceVerifier:
    """Verify citations via multi-strategy web search with word-overlap scoring."""

    def __init__(self, search_fn: Callable[[str, int], list[dict[str, str]]]):
        self.search_fn = search_fn

    def verify_citations(
        self, sources: list[str]
    ) -> list[VerificationResult]:
        """Verify a list of citation strings."""
        results: list[VerificationResult] = []
        for citation in sources:
            # Temporal-impossibility check runs BEFORE web search.
            # If the citation references a date that can't exist yet
            # (fabricated future arxiv ID, year-in-the-future), short-
            # circuit with unverified — no point asking the web to find
            # a paper that doesn't exist yet.
            temporal_fail = detect_temporal_impossibility(citation)
            if temporal_fail is not None:
                results.append(
                    VerificationResult(
                        citation=citation,
                        verified=False,
                        confidence="low",
                        note=f"temporal_impossibility: {temporal_fail}",
                    )
                )
                continue
            result = self._verify_single(citation)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Multi-strategy verification pipeline
    # ------------------------------------------------------------------

    def _verify_single(self, citation: str) -> VerificationResult:
        """Verify one citation using up to 3 search strategies."""
        parsed = self._parse_citation(citation)

        title = parsed.get("title", "")
        title_short = parsed.get("title_short", "")
        authors_list = parsed.get("authors_list", [])
        author_last = parsed.get("author_last", "")
        year = parsed.get("year", "")
        publisher = parsed.get("publisher", "")
        ctype = parsed.get("citation_type", "unknown")
        is_manuscript = ctype == "manuscript"

        if not title and not author_last:
            # Manuscript floor: if we recognized a repository, give MEDIUM
            if is_manuscript:
                return VerificationResult(
                    citation=citation,
                    verified=True,
                    confidence="medium",
                    note="Recognized repository/primary source (search not attempted)",
                )
            return VerificationResult(
                citation=citation,
                verified=False,
                confidence="low",
                note="Could not parse author or title from citation",
            )

        # Build search strategies
        strategies = self._build_strategies(
            author_last, title_short, year, publisher, ctype, authors_list,
        )

        best_result: VerificationResult | None = None

        for strategy_name, query in strategies:
            query = _normalize_for_search(query).strip()
            if not query or len(query) < 5:
                continue

            try:
                results = self.search_fn(query, 5)
            except Exception:
                continue

            if not results or (len(results) == 1 and "error" in results[0]):
                continue

            scored = self._score_results(
                citation, title, authors_list, results, strategy_name,
            )

            if scored.confidence == "high":
                return scored  # Early exit on HIGH

            # Keep the best non-HIGH result
            if best_result is None or (
                scored.confidence == "medium" and best_result.confidence == "low"
            ):
                best_result = scored

        # If no strategy produced results, apply manuscript floor
        if best_result is None:
            if is_manuscript:
                return VerificationResult(
                    citation=citation,
                    verified=True,
                    confidence="medium",
                    note="Recognized repository/primary source (no search results)",
                )
            return VerificationResult(
                citation=citation,
                verified=False,
                confidence="low",
                note="No search results across all strategies",
            )

        # Apply manuscript floor to best result
        if is_manuscript and best_result.confidence == "low":
            best_result = VerificationResult(
                citation=citation,
                verified=True,
                confidence="medium",
                matching_url=best_result.matching_url,
                note=f"{best_result.note} (elevated: recognized repository)",
            )

        return best_result

    # ------------------------------------------------------------------
    # Strategy builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_strategies(
        author_last: str,
        title_short: str,
        year: str,
        publisher: str,
        ctype: str,
        authors_list: list[str],
    ) -> list[tuple[str, str]]:
        """Build 2-3 search strategies based on parsed citation components."""
        strategies: list[tuple[str, str]] = []

        # Strategy 1: Relaxed keywords (no quotes) — always first
        parts = []
        if author_last:
            parts.append(author_last)
        if title_short:
            parts.append(title_short)
        if year:
            parts.append(year)
        if ctype == "ancient" and "translation" not in title_short.lower():
            parts.append("translation")
        if parts:
            strategies.append(("relaxed", " ".join(parts)))

        # Strategy 2: Title-focused
        parts2 = []
        if title_short:
            parts2.append(title_short)
        if publisher:
            parts2.append(publisher)
        elif year:
            parts2.append(year)
        if parts2 and len(parts2[0]) > 3:
            strategies.append(("title_focused", " ".join(parts2)))

        # Strategy 3: Author + context
        if author_last and (publisher or year):
            parts3 = [author_last]
            if publisher:
                parts3.append(publisher)
            if year:
                parts3.append(year)
            # Only add if meaningfully different from strategy 1
            s3 = " ".join(parts3)
            if not strategies or s3 != strategies[0][1]:
                strategies.append(("author_context", s3))

        return strategies[:3]  # Cap at 3

    # ------------------------------------------------------------------
    # Word-overlap scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_results(
        citation: str,
        title: str,
        authors_list: list[str],
        results: list[dict[str, str]],
        strategy_name: str,
    ) -> VerificationResult:
        """Score search results using word-overlap matching."""
        title_words = _tokenize_significant(title) if title else set()
        author_lasts_lower = {n.lower() for n in authors_list if n}

        best_overlap = 0.0
        author_found = False
        best_url = ""

        for r in results:
            if "error" in r:
                continue

            text = (
                r.get("snippet", "") + " " + r.get("title", "")
            ).lower()
            text_words = _tokenize_significant(text)
            url = r.get("url", "")

            # Title word overlap
            if title_words:
                overlap = len(title_words & text_words) / len(title_words)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_url = best_url or url

            # Author last name check (word boundary)
            for aln in author_lasts_lower:
                if aln in text_words or aln in text:
                    author_found = True
                    best_url = best_url or url
                    break

        # Scoring thresholds
        if best_overlap >= 0.6:
            return VerificationResult(
                citation=citation,
                verified=True,
                confidence="high",
                matching_url=best_url,
                note=f"Title overlap {best_overlap:.0%} via {strategy_name}",
            )
        if best_overlap >= 0.3 and author_found:
            return VerificationResult(
                citation=citation,
                verified=True,
                confidence="high",
                matching_url=best_url,
                note=f"Title overlap {best_overlap:.0%} + author confirmed via {strategy_name}",
            )
        if author_found:
            return VerificationResult(
                citation=citation,
                verified=True,
                confidence="medium",
                matching_url=best_url,
                note=f"Author found via {strategy_name} (title overlap {best_overlap:.0%})",
            )
        if best_overlap >= 0.3:
            return VerificationResult(
                citation=citation,
                verified=True,
                confidence="medium",
                matching_url=best_url,
                note=f"Partial title overlap {best_overlap:.0%} via {strategy_name}",
            )

        return VerificationResult(
            citation=citation,
            verified=False,
            confidence="low",
            note=f"Weak match via {strategy_name} (title overlap {best_overlap:.0%})",
        )

    # ------------------------------------------------------------------
    # Citation parser (enriched output)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_citation(citation: str) -> dict[str, str | list[str]]:
        """Extract author, title, year, publisher, and type from a citation string.

        Returns an enriched dict:
            author: full primary author string
            author_last: primary author last name (for search)
            authors_list: all author last names
            title: full extracted title
            title_short: first 5-6 significant words of title (for search)
            year: 4-digit year string
            publisher: extracted publisher name
            citation_type: book|paper|ancient|manuscript|unknown
        """
        result: dict[str, str | list[str]] = {}

        # --- Year ---
        year_match = re.search(r"\b(1\d{3}|20\d{2})\b", citation)
        if year_match:
            result["year"] = year_match.group(1)

        # --- Title extraction (priority order) ---

        # 1. Quoted or italic/bold markers (highest priority)
        title_match = re.search(
            r'["\u201c](.+?)["\u201d]|_(.+?)_|\*(.+?)\*', citation
        )
        if title_match:
            result["title"] = (
                title_match.group(1)
                or title_match.group(2)
                or title_match.group(3)
            )

        # 2. Comma-delimited: "Author, Title, trans./ed./year"
        #    Try this BEFORE period-delimited since "Author, Title, trans."
        #    is the most common format for classical/scholarly citations.
        if "title" not in result:
            comma_parts = citation.split(",")
            for i, part in enumerate(comma_parts[1:], start=1):
                candidate = part.strip()
                # Skip translator/editor annotations, years, volume markers
                if re.match(r"^\d{4}", candidate):
                    continue
                if re.match(r"^(?:trans|ed|vol|no|pp)\b", candidate, re.I):
                    continue
                if TRANS_ED_RE.match(candidate):
                    continue
                # Skip likely first names: single short capitalized word
                # after author, but NOT if it looks like a real title word
                # (Latin titles like "Enneads", "Meditations", "De Fato")
                if (
                    len(candidate.split()) == 1
                    and len(candidate) < 10
                    and i == 1
                    and candidate[0].isupper()
                    and candidate[1:].islower()
                    # Allow if it could be a Latin/Greek title word
                    and not any(candidate.lower().endswith(s) for s in (
                        "s", "ics", "ogy", "ion", "ium", "ons", "ics",
                        "sis", "tos", "ids", "ads", "ias",
                    ))
                ):
                    continue
                # Accept titles as short as 4 chars (e.g., "Enneads", "De Fato")
                if len(candidate) >= 4:
                    result["title"] = candidate
                    break

        # 3. Period-delimited: "Author. Title Text Here. Publisher, Year."
        if "title" not in result:
            period_parts = citation.split(".")
            for i, part in enumerate(period_parts[1:], start=1):
                candidate = part.strip()
                # Skip short segments (initials, abbreviations, "ed.", "trans.")
                if (
                    len(candidate) > 15
                    and not re.match(r"^(?:trans|ed|vol|no|pp)\b", candidate, re.I)
                    and not re.match(r"^\d", candidate)
                ):
                    result["title"] = candidate
                    break

        # --- Title short (first 6 significant words for search) ---
        title = result.get("title", "")
        if title:
            sig_words = [
                w for w in re.findall(r"[A-Za-z\u00C0-\u024F]{2,}", title)
                if w.lower() not in TITLE_STOP_WORDS
            ]
            result["title_short"] = " ".join(sig_words[:6])
        else:
            result["title_short"] = ""

        # --- Author extraction ---
        # Get the segment before the first title marker or period-delimited title
        author_segment = ""
        # Find first "real" period (not initials like "A." or "J.S.")
        # A real period is one NOT preceded by a single uppercase letter
        first_period = -1
        for m in re.finditer(r"\.", citation):
            pos = m.start()
            # Skip if this period follows a single letter (initial)
            if pos >= 1 and citation[pos - 1].isalpha() and (
                pos < 2 or not citation[pos - 2].isalpha()
            ):
                continue
            first_period = pos
            break

        first_comma = citation.find(",")
        first_paren = citation.find("(")

        # Choose the earliest delimiter for author boundary
        boundaries = [b for b in [first_period, first_comma, first_paren] if b > 0]
        if boundaries:
            boundary = min(boundaries)
            author_segment = citation[:boundary].strip()
        else:
            # No delimiters — try first 60 chars
            author_segment = citation[:60].strip()

        # Filter out non-author segments
        if author_segment:
            # Filter: too short, is a year, starts with "The"/"http"
            if (
                len(author_segment) < 2
                or re.match(r"^\d{4}$", author_segment)
                or author_segment.lower().startswith(("http", "www"))
            ):
                author_segment = ""

        if author_segment:
            result["author"] = author_segment
            result["author_last"] = _extract_last_name(author_segment)
            result["authors_list"] = _extract_all_last_names(
                citation, author_segment
            )
        else:
            result["author"] = ""
            result["author_last"] = ""
            result["authors_list"] = []

        # --- Publisher ---
        pub_match = PUBLISHER_RE.search(citation)
        result["publisher"] = pub_match.group(0) if pub_match else ""

        # --- Citation type ---
        year_str = result.get("year", "")
        result["citation_type"] = _classify_citation(
            citation, year_str if isinstance(year_str, str) else ""
        )

        return result
