"""PDF image + page-render extraction via PyMuPDF.

PyMuPDF (imported as `pymupdf` or `fitz`) handles two jobs that
`pypdf` can't:

1. **Rendering sparse pages** — when a PDF page is mostly a figure or
   diagram, `pypdf.extract_text()` returns <150 characters and we'd
   silently lose the page's content. This module detects those pages
   and renders them as PNGs into the vault's `raw/assets/<slug>/`
   directory so the visual information is preserved.

2. **Extracting embedded bitmaps** — any image resource embedded in
   the PDF (architecture diagrams, charts, screenshots, figures) is
   extracted as a PNG and stored alongside. Very small images
   (icons, logos) are filtered out.

Outputs are *additive* — the companion `.md` gets markdown image
references pointing at the saved PNGs, so a future image-aware
PageWriter (or a human browsing the vault in Obsidian) can see the
visual content next to the surrounding text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class PageData:
    """One page's worth of extracted content."""

    page_num: int                               # 1-indexed
    text: str                                   # extracted text
    rendered_image: Path | None = None          # full-page PNG (if sparse)
    embedded_images: list[Path] = field(default_factory=list)


def _import_fitz():
    """Try modern `pymupdf` first, fall back to legacy `fitz` import."""
    try:
        import pymupdf as fitz  # type: ignore
        return fitz
    except ImportError:
        try:
            import fitz  # type: ignore
            return fitz
        except ImportError:
            return None


def extract_pdf_with_figures(
    pdf_path: Path,
    assets_dir: Path,
    *,
    text_threshold: int = 150,
    dpi: int = 120,
    max_embedded_per_page: int = 4,
    max_embedded_total: int = 30,
    min_embedded_size: tuple[int, int] = (100, 100),
    force_render_all: bool = False,
    drop_text: bool = False,
) -> list[PageData]:
    """Extract per-page text plus figures/renders from a PDF.

    For each page, we:
    - Run `get_text("text")` to pull plain text.
    - If the extracted text is under `text_threshold` chars, render
      the full page at `dpi` and save to `assets_dir/page-NNN.png`.
    - Extract embedded bitmaps above `min_embedded_size` to
      `assets_dir/fig-pNNN-MM.png`, capped per-page and total.

    `force_render_all`: render EVERY page regardless of text threshold.
    Use for scanned PDFs where text extraction produces garbage (broken
    font cmap, image-only pages, etc.).

    `drop_text`: ignore whatever `get_text` returned. Paired with
    `force_render_all` for OCR-only mode — prevents gibberish from the
    broken cmap contaminating the downstream .md.

    Returns one `PageData` per page. Empty list if PyMuPDF isn't
    installed or the PDF can't be opened.
    """
    fitz = _import_fitz()
    if fitz is None:
        log.warning("pymupdf not installed; figure extraction skipped")
        return []

    assets_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.warning("pymupdf couldn't open %s: %s", pdf_path, exc)
        return []

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[PageData] = []
    total_embedded = 0

    try:
        for i, page in enumerate(doc, start=1):
            text = ""
            try:
                text = page.get_text("text") or ""
            except Exception:
                pass
            text = text.strip()
            if drop_text:
                text = ""

            rendered: Path | None = None
            if force_render_all or len(text) < text_threshold:
                try:
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    rendered = assets_dir / f"page-{i:03d}.png"
                    pix.save(str(rendered))
                    pix = None
                except Exception:
                    rendered = None

            embedded: list[Path] = []
            try:
                images_on_page = page.get_images(full=True)
            except Exception:
                images_on_page = []

            for img_idx, img_info in enumerate(images_on_page):
                if total_embedded >= max_embedded_total:
                    break
                if len(embedded) >= max_embedded_per_page:
                    break
                xref = img_info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if (
                        pix.width < min_embedded_size[0]
                        or pix.height < min_embedded_size[1]
                    ):
                        pix = None
                        continue
                    # PyMuPDF returns CMYK or DeviceN for some PDFs; convert.
                    if pix.colorspace and pix.colorspace.n >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    target = assets_dir / f"fig-p{i:03d}-{img_idx:02d}.png"
                    pix.save(str(target))
                    embedded.append(target)
                    total_embedded += 1
                    pix = None
                except Exception:
                    continue

            pages.append(
                PageData(
                    page_num=i,
                    text=text,
                    rendered_image=rendered,
                    embedded_images=embedded,
                )
            )
    finally:
        doc.close()

    return pages


OCR_PAGE_PROMPT = """\
You are performing OCR on one page of a scanned book. Transcribe ALL
visible text verbatim in natural reading order (top-to-bottom, left-
to-right, following columns). Preserve:

- Paragraph breaks (blank line between paragraphs)
- Chapter/section headings as markdown headings (# for chapter,
  ## for section, ### for subsection)
- Italics as *text*, bold as **text** (only where clearly typeset,
  don't guess)
- Lists as `- item` or `1. item` preserving original markers
- Footnotes: append at end of page with a `---` separator, preserving
  the original marker numbers/symbols
- Page number (if visible): note on its own line at the end as
  `(page N)` or `(page iv)` matching what's shown.
- Captions: under the figure/image as `**Figure N.** caption text`
- Equations: inline as `\\( ... \\)` or block as `\\[ ... \\]`. Keep
  variable letters, operators, and grouping exact.

If a diagram, plate, or figure is on the page:
1. Transcribe any visible text/labels/captions.
2. Add a paragraph immediately after titled `[FIGURE DESCRIPTION]`
   describing what the figure shows — shapes, lines, proportional
   relationships, geometric armatures, any angle/ratio labels, arrows,
   numbering — in concrete detail. This is a compositional-analysis
   wiki; geometric specifics matter.

Do NOT summarize or paraphrase the body text. Do NOT skip passages
you find uninteresting. Do NOT invent content that isn't on the page.
If part of the page is illegible, mark `[illegible]` rather than
guessing.

Return only the transcription — no preamble, no "Here is the
transcription:" header, just the content.
"""


_SUBSTANTIVE_FIGURE_MARKERS = (
    # Explicit figure/table/algorithm/equation captions
    r"\bFig(?:ure|\.)\s*\d+",
    r"\bTable\s+\d+",
    r"\bAlgorithm\s+\d+",
    r"\bEquation\s+\d+",
    r"\bEq\.\s*\d+",
    # Phrases that signal the page references a figure's content
    r"\bshown\s+in\s+Fig",
    r"\bas\s+(?:shown|illustrated|depicted|summarized)\s+in",
    r"\billustrat(?:es|ed|ing)",
    r"\bdepicts?",
    # Code / pseudocode markers
    r"```",
    r"\b(?:def|class|function|import)\s+\w+",
    r"\bInput:\s",
    r"\bOutput:\s",
    # Formal notation
    r"\\\\\(|\\\\\[",  # LaTeX inline/display math (quadruple-escaped for regex)
    r"\$\w",
)


def _is_substantive_figure_context(page: "PageData") -> bool:
    """Heuristic: does this page's text justify Vision-transcribing its figures?

    Returns True if the page has explicit figure/table/algorithm
    references, code markers, or formal notation. Returns False for
    pages of pure narrative prose whose embedded images are likely
    decorative (stock diagrams, header flourishes, page-break images).

    Rendered full-page images (page.rendered_image) are ALWAYS
    considered substantive because the render-fallback triggers on
    sparse pages — which are precisely the pages whose content is in
    the figure, not the text.
    """
    import re as _re

    if page.rendered_image:
        return True
    text = page.text or ""
    if not text:
        # No text to mine for references. Keep the figures — they may be
        # the only content on the page (e.g., a figure-spread).
        return True
    for pattern in _SUBSTANTIVE_FIGURE_MARKERS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return True
    return False


def describe_pdf_figures(
    pages: list[PageData],
    client=None,
    model: str | None = None,
    cost_tracker=None,
    *,
    provider=None,
    max_figures: int | None = 40,
    max_workers: int = 8,
    prompt: str | None = None,
    only_rendered_pages: bool = False,
    batch_max_wait_seconds: int = 1800,
    vision_gate: bool = True,
) -> dict[str, str]:
    """Run vision transcription over every figure found by `extract_pdf_with_figures`.

    Returns a mapping of `str(image_path) -> dense description`. The
    description preserves any code the figure contains as fenced blocks
    (per the view_image default prompt), plus prose for layout, axis
    labels, numeric values, etc. This is the eager-transcription step
    that makes figure content visible to the text-only Router/Writer.

    Backend selection is delegated to the `VisionProvider`:

    - `AnthropicVisionProvider` (Claude Vision) uses the Message Batches
      API when N >= 3, falling back to parallel `messages.create`.
    - `OllamaVisionProvider` (local Gemma 4) uses a threadpool over
      individual /api/chat calls — the local GPU batches at the kernel
      level, no server-side batch API exists to exploit.

    Both call styles are supported:

    - Pass a `provider` directly (new style).
    - Pass `client + model + cost_tracker` (legacy style) — an
      `AnthropicVisionProvider` is built from them transparently. This
      keeps older ingest paths working through the migration.

    `max_figures=None` means unbounded (required for full-book OCR of
    scanned PDFs — a 250-page book has 250 "figures").

    `prompt` overrides the default view_image prompt. Pass
    `OCR_PAGE_PROMPT` for scanned-book OCR mode.

    `only_rendered_pages`: in OCR mode the embedded-image extraction
    often yields noisy duplicates of the rendered page (since scans
    tend to have the whole page as one embedded bitmap). Set this to
    skip `embedded_images` and process only the `rendered_image` entries.
    """
    from compendium.sources.vision_provider import (
        AnthropicVisionProvider,
        IMAGE_MEDIA_TYPES,
        VisionRequest,
    )

    if provider is None:
        if client is None or model is None or cost_tracker is None:
            raise TypeError(
                "describe_pdf_figures needs either a `provider` or "
                "`(client, model, cost_tracker)` for the legacy path"
            )
        provider = AnthropicVisionProvider(
            client=client,
            model=model,
            cost_tracker=cost_tracker,
            batch_max_wait_seconds=batch_max_wait_seconds,
        )

    # Gather all figure paths up front, respecting the max_figures cap.
    # `vision_gate=True` drops embedded images on pages that look like
    # narrative prose with no figure/table/algorithm references — they're
    # usually decorative and not worth the per-figure Vision cost (free
    # on local Gemma, ~$0.01 on Claude — still nice to skip).
    # Rendered full-page images are ALWAYS transcribed (the render
    # fallback triggers on sparse pages = diagram-heavy content).
    targets: list[Path] = []
    skipped_decorative = 0
    cap = max_figures if max_figures is not None else 10**9
    for page in pages:
        include_embedded = (
            (not vision_gate)
            or _is_substantive_figure_context(page)
        )
        if page.rendered_image:
            targets.append(page.rendered_image)
        if not only_rendered_pages:
            for img in page.embedded_images:
                if include_embedded:
                    targets.append(img)
                else:
                    skipped_decorative += 1
        if len(targets) >= cap:
            break
    targets = targets[:cap]
    if skipped_decorative:
        log.info(
            "vision gate: skipped %d embedded figure(s) on pages with no "
            "figure/table/algorithm references", skipped_decorative,
        )
    if not targets:
        return {}

    prompt_text = (prompt or _default_vision_prompt()).strip()

    # Build requests, filtering out unreadable / unsupported images up
    # front so the provider doesn't waste a call on them.
    requests: list[VisionRequest] = []
    request_paths: list[Path] = []
    for idx, img in enumerate(targets):
        media_type = IMAGE_MEDIA_TYPES.get(img.suffix.lower())
        if media_type is None:
            log.debug("skip unsupported format: %s", img)
            continue
        try:
            data = img.read_bytes()
        except OSError as exc:
            log.debug("skip unreadable image %s: %s", img, exc)
            continue
        requests.append(VisionRequest(
            image_bytes=data,
            media_type=media_type,
            prompt=prompt_text,
            custom_id=f"fig-{idx:04d}",
        ))
        request_paths.append(img)

    if not requests:
        return {}

    def _progress(done: int, total: int) -> None:
        log.info("figure-transcription: %d/%d done", done, total)

    results = provider.describe_many(
        requests,
        max_workers=max_workers,
        progress_cb=_progress,
    )

    descriptions: dict[str, str] = {}
    drop_oversize: list[Path] = []
    drop_api_error: list[tuple[Path, str]] = []
    drop_empty: list[Path] = []
    decorative_count = 0
    for img, desc in zip(request_paths, results):
        if not desc:
            drop_empty.append(img)
            continue
        if desc.startswith("[IMAGE TOO LARGE]"):
            drop_oversize.append(img)
            continue
        if desc.startswith("[IMAGE "):
            drop_api_error.append((img, desc.split("]", 1)[0] + "]"))
            continue
        normalized = _normalize_decorative_response(desc)
        if normalized is not None:
            descriptions[str(img)] = normalized
            decorative_count += 1
        else:
            descriptions[str(img)] = desc[:6_000]
    if drop_oversize or drop_api_error or drop_empty or decorative_count:
        log.info(
            "figure-transcription — kept: %d (incl. %d decorative one-liners) "
            "/ %d; drops — oversize: %d, api-error: %d, empty: %d",
            len(descriptions), decorative_count, len(request_paths),
            len(drop_oversize), len(drop_api_error), len(drop_empty),
        )
        for img in drop_oversize:
            log.info("  oversize: %s", img.name)
        for img, kind in drop_api_error:
            log.info("  %s: %s", kind.lower(), img.name)
        for img in drop_empty:
            log.info("  empty-response: %s (model returned 0 chars)", img.name)
    return descriptions


# Marker prefix the renderer keys off to swap a `### Figure
# transcription` block for a one-line italic caption. Kept as a module
# constant so `render_pages_as_markdown` and any future consumer agree
# on the contract.
DECORATIVE_PREFIX = "[DECORATIVE]"


def _normalize_decorative_response(desc: str) -> str | None:
    """Detect a `[DECORATIVE] ...` reply and normalize it.

    Returns the normalized one-liner (always starts with the canonical
    `DECORATIVE_PREFIX`) when the model's response classifies the figure
    as decorative — even if the model wandered past one line, we keep
    only the first non-empty line and trim to ~140 chars so a misbehaving
    response can't bloat the markdown.

    Returns `None` for substantive responses.

    The matcher is tolerant: case-insensitive, accepts `[DECORATIVE]:`
    with or without the colon, and strips leading whitespace. Small
    vision models don't always emit the prefix exactly as instructed.
    """
    stripped = desc.lstrip()
    head = stripped[:32].upper()
    if not head.startswith("[DECORATIVE]"):
        return None
    # Take the first non-empty line, drop the bracket prefix, and cap.
    first_line = next(
        (ln.strip() for ln in stripped.splitlines() if ln.strip()),
        "",
    )
    body = first_line[len("[DECORATIVE]"):].lstrip(": ").strip()
    if not body:
        body = "Decorative element — no technical content."
    return f"{DECORATIVE_PREFIX} {body[:140]}"


def _default_vision_prompt() -> str:
    """Return the same default prompt `view_image` uses, lazily.

    Avoids duplicating the long prompt string and guarantees every
    transcription path (REPL, batch, local) produces comparable output.
    """
    from compendium.repl.functions import _DEFAULT_VIEW_IMAGE_PROMPT  # type: ignore[attr-defined]

    return _DEFAULT_VIEW_IMAGE_PROMPT


def render_pages_as_markdown(
    pages: list[PageData],
    assets_rel_base: str,
    *,
    max_chars: int = 400_000,
    figure_descriptions: dict[str, str] | None = None,
) -> str:
    """Render extracted page data as inline markdown.

    `assets_rel_base` is the relative path from the consuming .md file
    to the assets directory (e.g., `../assets/rlm-paper`).

    Each page section gets:
    - a `## [page N]` heading
    - a rendered-page `![...](path)` reference if the page was sparse
    - if `figure_descriptions` contains that image's path, the
      description is inlined under a `### Figure transcription`
      subheading — this is where code blocks from figures become
      text-visible
    - the page's extracted text (if any)
    - embedded-image refs (each with their own inlined transcription
      if present)

    Pages with zero content of any kind are skipped.
    """
    descs = figure_descriptions or {}
    parts: list[str] = []
    running = 0
    for page in pages:
        if not page.text and not page.rendered_image and not page.embedded_images:
            continue
        block: list[str] = [f"## [page {page.page_num}]", ""]
        if page.rendered_image:
            rel = f"{assets_rel_base}/{page.rendered_image.name}"
            block.append(
                f"![Page {page.page_num} — rendered (text extraction was sparse)]({rel})"
            )
            block.append("")
            _append_description(block, descs.get(str(page.rendered_image)))
        if page.text:
            block.append(page.text)
            block.append("")
        for img in page.embedded_images:
            rel = f"{assets_rel_base}/{img.name}"
            block.append(f"![Figure from page {page.page_num}]({rel})")
            block.append("")
            _append_description(block, descs.get(str(img)))
        section = "\n".join(block).rstrip()
        parts.append(section)
        running += len(section)
        if running > max_chars:
            parts.append("\n[TRUNCATED — remaining pages omitted]")
            break
    return "\n\n".join(parts)[:max_chars]


def _append_description(block: list[str], desc: str | None) -> None:
    """Append a figure description to `block`, formatted by kind.

    Decorative figures (those whose description is the marker line
    produced by `_normalize_decorative_response`) get a single italic
    caption — no `### Figure transcription` heading, no bulk text,
    just enough to tell a reader (or a downstream LLM) that we looked
    at the image and it carries no technical payload.

    Substantive figures get the full block as before.
    """
    if not desc:
        return
    if desc.startswith(DECORATIVE_PREFIX):
        # Strip the marker; emit only the human-facing phrase as italic.
        caption = desc[len(DECORATIVE_PREFIX):].strip()
        block.append(f"*Decorative — {caption}*" if caption else "*Decorative.*")
        block.append("")
        return
    block.append("### Figure transcription")
    block.append("")
    block.append(desc)
    block.append("")


def summarize_extraction(pages: list[PageData]) -> dict[str, int]:
    """Compact stats for logging."""
    return {
        "pages": len(pages),
        "text_chars": sum(len(p.text) for p in pages),
        "rendered_pages": sum(1 for p in pages if p.rendered_image),
        "embedded_figures": sum(len(p.embedded_images) for p in pages),
    }
