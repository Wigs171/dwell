"""Split a long-form PDF (book, thesis, report) into chapter-sized
markdown chunks that the standard `ingest` pipeline can consume one
at a time.

Why this exists: Compendium's Router caps at `max_pages_per_ingest`
(default 25) and `max_cost_dollars` per invocation. A 600-page book
passed to one ingest call hits both ceilings and produces a shallow
~25-page vault representation of a very deep source. The scale
pattern this system is built for is **split + iterate**: carve the
book into chapter-sized chunks, ingest each one as a normal source
(registry dedups by hash), and let `loop --resume` compound across
sessions.

This module is a pure preprocessor. It does NOT invoke Router /
PageWriter / Reviewer. It reads a PDF, extracts text (native or via
the configured VisionProvider's OCR path), carves along the table of
contents (or fixed page windows as a fallback), and writes each
chunk to `<vault>/raw/articles/<book-slug>-<NN>-<chunk-slug>.md` in
the standard raw-source format: provenance frontmatter, chapter H1,
body text, `## Sources` section pointing back to the book PDF.

Design choices worth calling out:

- **No tags, no embeddings.** Compendium's retrieval mechanism is
  structural (wikilink graph + Explorer's gap finder), not semantic.
  Adding tags or vectors would create a competing source of truth
  that no existing agent consumes. Provenance in frontmatter is
  sufficient for Router to read the chapter as a normal source.
- **TOC-driven split wins when available.** Books typically have
  meaningful chapter boundaries; fixed-page windows are a fallback
  for TOC-less scans.
- **OCR path reuses the configured VisionProvider.** With
  `COMPENDIUM_VISION_PROVIDER=ollama`, a scanned 600-page book
  becomes a $0 overnight job.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from compendium.sources.vision_provider import (
    IMAGE_MEDIA_TYPES,
    VisionRequest,
)


log = logging.getLogger(__name__)


@dataclass
class Chunk:
    """One chapter-sized piece of a book.

    `page_start` and `page_end` are 1-indexed, inclusive. `text` is
    the concatenated body text for the page range (native-extracted
    or OCR'd). `title` becomes the H1 heading inside the output
    markdown and drives the output filename slug.
    """

    index: int  # 1-indexed chunk number, stable across re-splits of the same book
    title: str
    page_start: int
    page_end: int
    text: str = ""
    source_ref: str = ""  # "ch1", "pp12-28", etc. — shown in frontmatter
    toc_level: int = 0  # 0 = fixed-window split; 1+ = TOC-derived
    toc_breadcrumb: list[str] = field(default_factory=list)


def _import_fitz():
    try:
        import pymupdf as fitz  # type: ignore
        return fitz
    except ImportError:
        try:
            import fitz  # type: ignore
            return fitz
        except ImportError:
            return None


# ---------------------------------------------------------------------------
# TOC-driven chunking
# ---------------------------------------------------------------------------


def extract_toc(pdf_path: Path, *, max_level: int = 1) -> list[tuple[int, str, int]]:
    """Return the PDF's table of contents up to `max_level`.

    PyMuPDF's `doc.get_toc()` returns `[[level, title, page], ...]`
    with 1-indexed pages and 1-indexed levels (1 = top-level).
    Empty list if the PDF has no outline or PyMuPDF is missing.
    """
    fitz = _import_fitz()
    if fitz is None:
        return []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.warning("pymupdf couldn't open %s: %s", pdf_path, exc)
        return []
    try:
        toc = doc.get_toc(simple=True) or []
    except Exception:
        toc = []
    finally:
        doc.close()
    return [(lvl, title.strip(), page) for lvl, title, page in toc if lvl <= max_level]


def plan_toc_chunks(
    pdf_path: Path,
    *,
    max_level: int = 1,
    min_pages: int = 2,
) -> list[Chunk]:
    """Turn a TOC into a list of `Chunk` objects with page ranges.

    Boundaries are inferred: chunk N ends at (chunk N+1 start - 1), and
    the last chunk runs to the document end. Chunks shorter than
    `min_pages` (typically front-matter / single-page section headers)
    are merged into the next chunk to keep ingest sizes reasonable.
    """
    fitz = _import_fitz()
    if fitz is None:
        return []

    toc = extract_toc(pdf_path, max_level=max_level)
    # Some PDFs emit TOC entries with invalid pages (e.g. external-link
    # outlines to sacred-texts.com at page -1). Drop those — they'd
    # otherwise produce zero-length chunks that confuse the splitter.
    toc = [(lvl, t, p) for lvl, t, p in toc if p and p > 0]
    if not toc:
        return []

    try:
        doc = fitz.open(str(pdf_path))
        total_pages = doc.page_count
        doc.close()
    except Exception:
        return []

    chunks: list[Chunk] = []
    stack: list[str] = []
    for i, (level, title, page) in enumerate(toc):
        # Maintain a breadcrumb (Part > Chapter > Section) for chunks at
        # deeper levels. Simple best-effort: truncate to `level-1` and
        # append current title.
        stack = stack[: level - 1]
        stack.append(title)

        if i + 1 < len(toc):
            next_page = toc[i + 1][2]
            end_page = max(page, next_page - 1)
        else:
            end_page = total_pages

        chunks.append(Chunk(
            index=len(chunks) + 1,
            title=title,
            page_start=page,
            page_end=end_page,
            source_ref=f"pp{page}-{end_page}",
            toc_level=level,
            toc_breadcrumb=list(stack),
        ))

    # Merge too-short chunks forward (typical pattern: a chapter opener
    # that's just a title page). Walk from the end so merges compose.
    merged: list[Chunk] = []
    for c in chunks:
        if (
            merged
            and (c.page_end - c.page_start + 1) < min_pages
            and merged
        ):
            # Extend previous chunk's range to absorb this one.
            prev = merged[-1]
            prev.page_end = max(prev.page_end, c.page_end)
            prev.source_ref = f"pp{prev.page_start}-{prev.page_end}"
        else:
            merged.append(c)

    # Re-number indices after merge so they're stable / sequential.
    for i, c in enumerate(merged, start=1):
        c.index = i
    return merged


# ---------------------------------------------------------------------------
# Fixed-window chunking (fallback)
# ---------------------------------------------------------------------------


def plan_fixed_chunks(
    pdf_path: Path,
    *,
    pages_per_chunk: int = 25,
    book_title_override: str | None = None,
) -> list[Chunk]:
    """Fallback when the PDF has no usable TOC.

    Carves the PDF into N-page windows. Chunks are titled
    `"<book-title> — pp X–Y"` for human readability; slugification
    downstream uses the page range for uniqueness.
    """
    fitz = _import_fitz()
    if fitz is None:
        return []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []
    try:
        total_pages = doc.page_count
        book_title = book_title_override or _pdf_title(doc) or pdf_path.stem
    finally:
        doc.close()

    chunks: list[Chunk] = []
    for idx, start in enumerate(range(1, total_pages + 1, pages_per_chunk), start=1):
        end = min(start + pages_per_chunk - 1, total_pages)
        chunks.append(Chunk(
            index=idx,
            title=f"{book_title} — pp {start}–{end}",
            page_start=start,
            page_end=end,
            source_ref=f"pp{start}-{end}",
            toc_level=0,
        ))
    return chunks


def _pdf_title(doc) -> str:
    """Best-effort book title from PDF metadata, else empty."""
    try:
        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip()
        return title
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Text extraction — native or OCR
# ---------------------------------------------------------------------------


def fill_chunks_native(pdf_path: Path, chunks: list[Chunk]) -> list[Chunk]:
    """Populate `chunk.text` using PyMuPDF's native text extraction.

    Fast, free, deterministic. Works when the PDF has a real text
    layer (digitally-native books, modern theses, most arxiv PDFs).
    Produces gibberish on scanned books with broken font cmaps — use
    `fill_chunks_ocr` for those.
    """
    fitz = _import_fitz()
    if fitz is None:
        return chunks
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.warning("pymupdf open failed for %s: %s", pdf_path, exc)
        return chunks
    try:
        for c in chunks:
            parts: list[str] = []
            for p in range(c.page_start, c.page_end + 1):
                # PyMuPDF is 0-indexed internally.
                if p - 1 < 0 or p - 1 >= doc.page_count:
                    continue
                try:
                    t = doc[p - 1].get_text("text") or ""
                except Exception:
                    t = ""
                t = t.strip()
                if t:
                    parts.append(f"<!-- page {p} -->\n{t}")
            c.text = "\n\n".join(parts)
    finally:
        doc.close()
    return chunks


# Default OCR prompt — same shape as pdf_image_extractor.OCR_PAGE_PROMPT
# but inlined here so the splitter has no build-order dependency on it.
_OCR_PROMPT = """\
Transcribe ALL visible text on this page verbatim, preserving:
- paragraph breaks (blank line between paragraphs)
- headings as markdown (# chapter, ## section)
- italics as *text*, bold as **text** where clearly typeset
- lists as `- item` or `1. item`
- footnotes at end of page after a `---` separator
- page number on its own line at the end if visible

If a diagram or figure is present, transcribe any text/labels and add
a brief [FIGURE] description. Do NOT summarize or paraphrase. Mark
illegible regions `[illegible]`. Return only the transcription, no
preamble.
"""


def fill_chunks_ocr(
    pdf_path: Path,
    chunks: list[Chunk],
    vision_provider,
    *,
    dpi: int = 180,
    max_workers: int = 4,
    assets_dir: Path | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[Chunk]:
    """Populate `chunk.text` by rendering each page to PNG and running
    it through the configured `VisionProvider`'s `describe_many`.

    With `COMPENDIUM_VISION_PROVIDER=ollama`, this is a $0 job that
    runs at roughly Gemma 4 E4B's steady-state 24s/page on an 8 GB
    GPU — ~4 hours for a 600-page book, overnight-friendly.

    Renders are written to `assets_dir` if provided (useful for
    debugging); otherwise held in memory only.
    """
    fitz = _import_fitz()
    if fitz is None:
        return chunks
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.warning("pymupdf open failed for %s: %s", pdf_path, exc)
        return chunks

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    # Build one big VisionRequest list across all chunks so the
    # provider can parallelize freely. Track page ownership so we can
    # stitch results back into per-chunk text afterward.
    requests: list[VisionRequest] = []
    owners: list[tuple[int, int]] = []  # (chunk_idx, page_num)

    try:
        for c_idx, c in enumerate(chunks):
            for p in range(c.page_start, c.page_end + 1):
                if p - 1 < 0 or p - 1 >= doc.page_count:
                    continue
                try:
                    pix = doc[p - 1].get_pixmap(matrix=matrix, alpha=False)
                    img_bytes = pix.tobytes("png")
                    pix = None
                except Exception as exc:
                    log.debug("render failed for page %d: %s", p, exc)
                    continue
                if assets_dir is not None:
                    try:
                        assets_dir.mkdir(parents=True, exist_ok=True)
                        (assets_dir / f"page-{p:04d}.png").write_bytes(img_bytes)
                    except Exception:
                        pass
                requests.append(VisionRequest(
                    image_bytes=img_bytes,
                    media_type="image/png",
                    prompt=_OCR_PROMPT,
                    custom_id=f"p{p:04d}",
                ))
                owners.append((c_idx, p))
    finally:
        doc.close()

    if not requests:
        return chunks

    results = vision_provider.describe_many(
        requests,
        max_workers=max_workers,
        max_tokens=2500,
        progress_cb=progress_cb,
    )

    # Stitch OCR results back into per-chunk text, in page order.
    per_chunk: dict[int, list[tuple[int, str]]] = {}
    for (c_idx, page_num), text in zip(owners, results):
        if not text or text.startswith("[IMAGE "):
            continue
        per_chunk.setdefault(c_idx, []).append((page_num, text.strip()))

    for c_idx, page_texts in per_chunk.items():
        page_texts.sort(key=lambda x: x[0])
        body = "\n\n".join(
            f"<!-- page {p} -->\n{t}" for p, t in page_texts
        )
        chunks[c_idx].text = body

    return chunks


# ---------------------------------------------------------------------------
# Markdown emit
# ---------------------------------------------------------------------------


def chunk_to_markdown(
    chunk: Chunk,
    *,
    book_title: str,
    book_slug: str,
    pdf_path: Path,
    source_hash: str = "",
) -> str:
    """Render a chunk as a self-contained ingestible markdown file.

    Format matches what `cli.py ingest` + `research_agent._provenance_header`
    produce for other raw sources: HTML comments carry provenance,
    H1 gives Router a named target, `## Sources` enables citation
    verification downstream.
    """
    # `source_url: file://...` is a harmless pseudo-URL that lets the
    # registry dedup by URL if the same book is split twice into the
    # same vault. Real URLs aren't available for local books.
    pseudo_url = f"file://{pdf_path.as_posix()}#{chunk.source_ref}"

    breadcrumb = " › ".join(chunk.toc_breadcrumb) if chunk.toc_breadcrumb else ""

    lines = [
        f"<!-- source_type: book-chunk -->",
        f"<!-- source_url: {pseudo_url} -->",
        f"<!-- book_title: {book_title} -->",
        f"<!-- book_slug: {book_slug} -->",
        f"<!-- chunk_index: {chunk.index} -->",
        f"<!-- chunk_range: {chunk.source_ref} -->",
        f"<!-- chunk_toc_level: {chunk.toc_level} -->",
    ]
    if breadcrumb:
        lines.append(f"<!-- chunk_breadcrumb: {breadcrumb} -->")
    if source_hash:
        lines.append(f"<!-- book_hash: {source_hash} -->")
    lines.append("")
    lines.append(f"# {chunk.title}")
    lines.append("")
    if breadcrumb and breadcrumb != chunk.title:
        lines.append(f"_{breadcrumb}_")
        lines.append("")
    lines.append(f"> Extracted from **{book_title}**, pages "
                 f"{chunk.page_start}–{chunk.page_end}.")
    lines.append("")
    lines.append(chunk.text.strip() or "_[no text extracted for this range]_")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    lines.append(f"- **{book_title}** "
                 f"(pp {chunk.page_start}–{chunk.page_end}) — local PDF: "
                 f"`{pdf_path.name}`")
    return "\n".join(lines) + "\n"


_MAX_SLUG_LEN = 50


def chunk_filename(book_slug: str, chunk: Chunk) -> str:
    """Deterministic filename: `<book-slug>-<NN>-<chunk-title-slug>.md`.

    The `NN` prefix keeps chunks ordered in the filesystem view. Title
    slugification drops non-ASCII and collapses whitespace; long
    titles are truncated to keep filesystem path limits in reach.
    """
    # Lazy import to avoid build-order dependency during a bare extraction.
    from compendium.vault.pages import slugify

    title_slug = slugify(chunk.title) or f"pp{chunk.page_start}-{chunk.page_end}"
    if len(title_slug) > _MAX_SLUG_LEN:
        title_slug = title_slug[:_MAX_SLUG_LEN].rstrip("-")
    return f"{book_slug}-{chunk.index:02d}-{title_slug}.md"


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


@dataclass
class SplitPlan:
    """Result of planning (before writing). Useful for `--dry-run`."""

    chunks: list[Chunk]
    book_title: str
    book_slug: str
    strategy: str  # "toc" | "fixed"
    toc_max_level: int = 0
    pages_per_chunk: int = 0


def plan_split(
    pdf_path: Path,
    *,
    strategy: str = "auto",
    toc_max_level: int = 1,
    pages_per_chunk: int = 25,
    min_pages_per_chunk: int = 2,
    book_title_override: str | None = None,
) -> SplitPlan:
    """Decide which split strategy to use and return the plan.

    `strategy="auto"` (default) tries TOC first, falls back to fixed
    windows if the PDF has no outline or the outline is degenerate
    (e.g., only one entry covering the whole book).
    """
    fitz = _import_fitz()
    if fitz is None:
        raise RuntimeError("pymupdf not installed; install it to split PDFs")

    # Reach into metadata once to pick a stable book title + slug.
    from compendium.vault.pages import slugify

    try:
        doc = fitz.open(str(pdf_path))
        meta_title = _pdf_title(doc)
        doc.close()
    except Exception:
        meta_title = ""
    book_title = book_title_override or meta_title or pdf_path.stem
    book_slug = slugify(book_title) or slugify(pdf_path.stem)

    chunks: list[Chunk] = []
    chosen = strategy
    if strategy in ("auto", "toc"):
        chunks = plan_toc_chunks(
            pdf_path,
            max_level=toc_max_level,
            min_pages=min_pages_per_chunk,
        )
        if chunks and len(chunks) >= 2:
            chosen = "toc"
        elif strategy == "toc":
            # Explicitly requested TOC split but it's unusable — raise
            # so the caller can surface a clean error.
            raise ValueError(
                f"TOC split requested but {pdf_path.name} has no "
                f"usable outline at level ≤{toc_max_level}"
            )
        else:
            chunks = []  # fall through to fixed

    if not chunks:
        chunks = plan_fixed_chunks(
            pdf_path,
            pages_per_chunk=pages_per_chunk,
            book_title_override=book_title,
        )
        chosen = "fixed"

    return SplitPlan(
        chunks=chunks,
        book_title=book_title,
        book_slug=book_slug,
        strategy=chosen,
        toc_max_level=toc_max_level if chosen == "toc" else 0,
        pages_per_chunk=pages_per_chunk if chosen == "fixed" else 0,
    )


def write_chunks(
    plan: SplitPlan,
    pdf_path: Path,
    out_dir: Path,
    *,
    source_hash: str = "",
    overwrite: bool = False,
) -> list[Path]:
    """Write every chunk in `plan` to `<out_dir>/<book-slug>-<NN>-<slug>.md`.

    Returns the list of files created (or skipped if `overwrite=False`
    and the file already exists).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for chunk in plan.chunks:
        fname = chunk_filename(plan.book_slug, chunk)
        out_path = out_dir / fname
        if out_path.exists() and not overwrite:
            log.info("skip existing chunk: %s", fname)
            written.append(out_path)
            continue
        md = chunk_to_markdown(
            chunk,
            book_title=plan.book_title,
            book_slug=plan.book_slug,
            pdf_path=pdf_path,
            source_hash=source_hash,
        )
        out_path.write_text(md, encoding="utf-8")
        written.append(out_path)
    return written
