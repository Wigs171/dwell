"""PDF download + text extraction for the research pipeline.

Given a URL that points at a PDF (arxiv, JSTOR, institutional repos),
download the binary and extract its text. The binary lives at
`raw/papers/<slug>.pdf` as archival provenance; the extracted text
goes alongside at `raw/papers/<slug>.md` and is what the ingest
pipeline actually reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx


_ARXIV_ABS_RE = re.compile(r"(https?://arxiv\.org/)abs/([^\s?#]+)", re.IGNORECASE)
_PAPER_HOSTS = (
    "arxiv.org",
    "jstor.org",
    "ssrn.com",
    "papers.ssrn.com",
    "semanticscholar.org",
    "biorxiv.org",
    "ncbi.nlm.nih.gov/pmc",
    "openreview.net",
    "dl.acm.org",
)


def looks_like_pdf_url(url: str) -> bool:
    """Heuristic: does this URL plausibly point at a paper / PDF?

    True when:
    - URL ends in .pdf (case-insensitive) before any query string
    - URL matches a known paper host

    False otherwise. This is a hint, not a contract — the downloader
    still verifies via Content-Type + magic bytes before saving.
    """
    if not url:
        return False
    lower = url.lower()
    bare = lower.split("?", 1)[0].split("#", 1)[0]
    if bare.endswith(".pdf"):
        return True
    return any(host in lower for host in _PAPER_HOSTS)


def normalize_paper_url(url: str) -> str:
    """Convert arxiv abstract URLs to PDF URLs (arxiv.org/abs/X -> /pdf/X.pdf).

    Other hosts are returned unchanged. Idempotent.
    """
    m = _ARXIV_ABS_RE.match(url)
    if m:
        base, paper_id = m.group(1), m.group(2).rstrip("/")
        if not paper_id.endswith(".pdf"):
            paper_id = paper_id + ".pdf"
        return f"{base}pdf/{paper_id}"
    return url


def download_pdf(url: str, target: Path, *, timeout: int = 90) -> bool:
    """Download a PDF to `target`. Returns True on success.

    Verifies the response is actually a PDF (Content-Type or %PDF magic)
    before writing. Creates parent directories.
    """
    try:
        url = normalize_paper_url(url)
        with httpx.Client(timeout=timeout, follow_redirects=True) as http:
            resp = http.get(
                url,
                headers={"User-Agent": "CompendiumBuilder/0.1 (research agent)"},
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            magic_ok = resp.content[:4] == b"%PDF"
            if "pdf" not in content_type and not magic_ok:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resp.content)
            return True
    except Exception:
        return False


def extract_pdf_text(path: Path, *, max_chars: int = 200_000) -> str:
    """Extract page-labeled text from a PDF using pypdf.

    Returns an empty string if pypdf isn't available or extraction fails.
    Each page is prefixed with `[page N]` so PageWriter can cite pages.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
    except Exception:
        return ""

    parts: list[str] = []
    running_len = 0
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        if not text:
            continue
        chunk = f"## [page {i}]\n\n{text}"
        parts.append(chunk)
        running_len += len(chunk)
        if running_len > max_chars:
            parts.append("\n[TRUNCATED — remaining pages omitted]\n")
            break
    return "\n\n".join(parts)[:max_chars]
