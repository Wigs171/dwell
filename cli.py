#!/usr/bin/env python3
"""
Compendium — an LLM-maintained Obsidian-compatible wiki.

Usage:
    python cli.py init <vault> --topic "..."
    python cli.py ingest <source> --vault <vault>
    python cli.py query "..." --vault <vault> [--file]
    python cli.py lint --vault <vault>
    python cli.py explore --vault <vault>
"""

from __future__ import annotations

import sys as _sys
if _sys.platform == "win32":
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
        _sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

import argparse
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def _not_yet(op: str, phase: str) -> None:
    console.print(f"[yellow]`{op}` is not yet implemented ({phase}).[/yellow]")
    sys.exit(2)


def cmd_init(args: argparse.Namespace) -> None:
    """Scaffold a new vault with CLAUDE.md and directory skeleton."""
    from compendium.vault import VaultPaths, render_claude_md, write_index
    from compendium.vault.log import append_entry

    paths = VaultPaths.for_vault(args.vault)
    if paths.is_initialized():
        console.print(
            f"[yellow]Vault already initialized at {paths.root} "
            f"(CLAUDE.md exists). Not overwriting.[/yellow]"
        )
        sys.exit(1)

    paths.root.mkdir(parents=True, exist_ok=True)
    for d in paths.all_dirs():
        d.mkdir(parents=True, exist_ok=True)

    paths.claude_md.write_text(render_claude_md(args.topic), encoding="utf-8")
    paths.log_md.write_text("# Log\n\n", encoding="utf-8")
    write_index(paths, topic=args.topic)

    # Seed an init entry so the log isn't empty.
    append_entry(
        paths,
        op="init",
        subject=args.topic,
        body=f"- vault root: `{paths.root.as_posix()}`\n- topic: {args.topic}",
    )

    console.print(f"[green]Initialized vault at[/green] [bold]{paths.root}[/bold]")
    console.print(f"  CLAUDE.md, index.md, log.md created")
    console.print(f"  Subdirs: raw/ wiki/{{entities,concepts,sources,syntheses,_meta}}")
    console.print(
        f"\n[dim]Next:[/dim] "
        f"[cyan]python cli.py ingest <source> --vault {paths.root}[/cyan]"
    )


def cmd_ingest(args: argparse.Namespace) -> None:
    """Ingest a source (local file or URL) into an existing vault."""
    from compendium.agents.ingest_orchestrator import (
        IngestOrchestrator,
        VaultNotInitialized,
    )
    from compendium.config import CompendiumConfig
    from compendium.models import ReviewSeverity
    from compendium.vault import VaultPaths

    paths = VaultPaths.for_vault(args.vault)
    config = CompendiumConfig()

    if args.max_cost is not None:
        config.max_cost_dollars = args.max_cost
    if args.model_strategic:
        config.model_strategic = args.model_strategic
    if args.model_synthesis:
        config.model_synthesis = args.model_synthesis
    if args.model_mechanical:
        config.model_mechanical = args.model_mechanical

    # --extract-only skips the Router/PageWriter pass entirely. Auth is
    # only required when those agents would run (or when the vision
    # provider is anthropic). With ollama vision + --extract-only there's
    # no Claude API call at all, so don't gate on auth.
    needs_anthropic = (
        not args.extract_only
        or (getattr(config, "vision_provider", "anthropic") or "anthropic").lower()
            == "anthropic"
    )
    if needs_anthropic and not config.has_auth:
        console.print(
            "[red]No API key found.[/red] Set ANTHROPIC_API_KEY in env or .env."
        )
        sys.exit(1)

    # --from-prompt: `source` is a research PROMPT, not a path. Run the
    # cheap non-REPL gather, then ingest each saved source via the
    # structured pipeline. Implies --structured.
    if getattr(args, "from_prompt", False):
        _cmd_ingest_from_prompt(args, config, paths)
        return

    orch = None
    if not args.extract_only:
        try:
            orch = IngestOrchestrator(
                config, paths, structured=getattr(args, "structured", False) or None
            )
        except VaultNotInitialized as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)

    source_arg = args.source
    if source_arg.startswith(("http://", "https://")):
        source_path = _fetch_url_to_raw(
            paths, source_arg, force=args.force_reingest
        )
        if source_path is None:
            sys.exit(1 if not args.allow_skip else 0)
    else:
        source_path = Path(source_arg)
        if not source_path.is_file():
            console.print(f"[red]Source not found:[/red] {source_path}")
            sys.exit(1)
        prepared = _prepare_local_source(
            paths,
            source_path,
            eager_vision=not args.no_eager_vision,
            force=args.force_reingest,
            force_ocr=args.force_ocr,
            ocr_dpi=args.ocr_dpi,
        )
        if prepared is None:
            # None can mean "already ingested, skip gracefully" or a real
            # failure. Either way, nothing more to do here.
            sys.exit(0)
        source_path = prepared

    if args.extract_only:
        console.print(
            f"\n[bold green]Extracted[/bold green] [bold]{source_path.name}[/bold] "
            f"[dim]→ {source_path.relative_to(paths.root).as_posix()}[/dim]"
        )
        console.print(
            "[dim]--extract-only: skipping Router/PageWriter. Re-run "
            "without the flag (or with `loop --resume`) to ingest into "
            "the wiki.[/dim]"
        )
        return

    console.print(f"[cyan]Ingesting[/cyan] {source_path} [cyan]→[/cyan] {paths.root}")

    progress = None
    if args.json_progress:
        import json as _json

        def progress(phase: str, payload: dict) -> None:
            # One compact JSON line per phase, sentinel-prefixed so a parent process
            # can pick it out of the normal console output. Plain print (not the rich
            # console) to avoid markup/wrapping; flush so it streams live.
            sys.stdout.write("@@PROG@@" + _json.dumps({"phase": phase, **payload}) + "\n")
            sys.stdout.flush()

    try:
        report = orch.ingest(source_path, progress=progress, run_explore=not args.no_explore)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Ingest failed: {exc}[/red]")
        sys.exit(1)

    console.print(
        f"\n[bold green]Ingested[/bold green] [bold]{report.source_title}[/bold] "
        f"(source_id: [cyan]{report.source_id}[/cyan])"
    )
    console.print(
        f"  [green]created[/green] {len(report.pages_created)} · "
        f"[yellow]updated[/yellow] {len(report.pages_updated)} · "
        f"[dim]implied wikilinks[/dim] {len(report.implied_wikilinks)}"
    )
    if report.pages_created:
        console.print("  [green]new pages:[/green] " + ", ".join(report.pages_created))
    if report.pages_updated:
        console.print("  [yellow]updated:[/yellow] " + ", ".join(report.pages_updated))
    warn_issues = [i for i in report.review_issues if i.severity != ReviewSeverity.INFO]
    if warn_issues:
        console.print(f"\n[yellow]Review issues ({len(warn_issues)}):[/yellow]")
        for issue in warn_issues[:10]:
            console.print(
                f"  - [{issue.severity.value}] [cyan]{issue.page_id}[/cyan] "
                f"[{issue.kind}] {issue.message}"
            )
        if len(warn_issues) > 10:
            console.print(f"  ...and {len(warn_issues) - 10} more")
    console.print(f"\n[dim]Cost: ${report.cost_dollars:.4f}[/dim]")


def _cmd_ingest_from_prompt(args, config, paths) -> None:
    """`ingest --from-prompt`: gather sources from a research prompt, then
    ingest each via the structured pipeline. Headless and cheap (no REPL).

    Emits the SAME `@@PROG@@{json}` lines as `cmd_ingest` under
    `--json-progress`, with extra gather phases (search / searched /
    gathered / gather_done) so a UI can follow the web-research portion.
    """
    import json as _json

    from compendium.agents.ingest_orchestrator import (
        IngestOrchestrator,
        VaultNotInitialized,
    )
    from compendium.agents.structured_ingest import structured_gather
    from compendium.models import ReviewSeverity
    from compendium.vault import IngestRegistry, append_entry

    if not config.has_auth:
        console.print(
            "[red]No API key found.[/red] Set ANTHROPIC_API_KEY in env or .env."
        )
        sys.exit(1)
    has_jina = bool(getattr(config, "jina_api_key", None))
    if config.search_provider == "none" and not has_jina:
        console.print(
            "[yellow]Warning: no search provider configured.[/yellow] "
            "--from-prompt requires web_search — set COMPENDIUM_SEARCH_PROVIDER "
            "(tavily, brave, or jina) and the matching key."
        )
        if not args.allow_skip:
            sys.exit(1)

    progress = None
    if args.json_progress:
        def progress(phase: str, payload: dict) -> None:
            sys.stdout.write("@@PROG@@" + _json.dumps({"phase": phase, **payload}) + "\n")
            sys.stdout.flush()

    # Structured orchestrator (forced on for --from-prompt).
    try:
        orch = IngestOrchestrator(config, paths, structured=True)
    except VaultNotInitialized as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    prompt = args.source
    console.print(f"[cyan]Gathering sources for prompt[/cyan] '[bold]{prompt}[/bold]'")
    registry = IngestRegistry(paths)
    # Share the orchestrator's cost tracker so gather + ingest spend is unified.

    def _gather_progress(phase: str, payload: dict) -> None:
        if progress is not None:
            try:
                s = orch.cost_tracker.get_summary()
                progress(phase, {**payload, "cost": s["estimated_cost_usd"]})
            except Exception:
                progress(phase, payload)

    try:
        saved = structured_gather(
            config, paths, prompt, registry,
            progress=_gather_progress if progress else None,
            cost_tracker=orch.cost_tracker,
        )
    except Exception as exc:
        console.print(f"[red]Gather failed: {exc}[/red]")
        sys.exit(1)

    if not saved:
        console.print("[yellow]No sources gathered for that prompt.[/yellow]")
        # Not a hard failure under --allow-skip (parity with dedup-skip).
        sys.exit(0 if args.allow_skip else 1)

    console.print(
        f"\n[bold green]{len(saved)} source"
        f"{'s' if len(saved) != 1 else ''} gathered → raw/articles/[/bold green]"
    )
    log_body = [f"- prompt: {prompt}"]
    for p in saved:
        console.print(f"  - [cyan]{p.name}[/cyan]")
        log_body.append(f"- gathered: `{p.as_posix()}`")
    append_entry(paths, op="gather", subject=prompt[:80], body="\n".join(log_body))

    # Ingest each gathered source via the structured pipeline. Skip
    # per-source explore (run one consolidated explore at the end).
    total_created = 0
    total_updated = 0
    total_issues = 0
    for p in saved:
        try:
            report = orch.ingest(p, progress=progress, run_explore=False)
        except Exception as exc:
            console.print(f"  [red]FAILED[/red] {p.name}: {exc}")
            continue
        total_created += len(report.pages_created)
        total_updated += len(report.pages_updated)
        total_issues += sum(
            1 for i in report.review_issues if i.severity != ReviewSeverity.INFO
        )
        console.print(
            f"  [green]✓[/green] {p.name} → "
            f"{len(report.pages_created)} created · "
            f"{len(report.pages_updated)} updated"
        )

    if not args.no_explore:
        try:
            exp = orch._explorer.explore()
            console.print(
                f"\n[green]explore[/green]: {len(exp.proposals)} proposals → "
                f"[cyan]wiki/_meta/expansion.md[/cyan]"
            )
        except Exception as exc:
            console.print(f"\n[yellow]explore failed: {exc}[/yellow]")

    total_cost = orch.cost_tracker.get_summary()["estimated_cost_usd"]
    console.print(
        f"\n[bold]totals:[/bold] [green]{total_created}[/green] created · "
        f"[yellow]{total_updated}[/yellow] updated · "
        f"[red]{total_issues}[/red] review issues"
    )
    console.print(f"[dim]Cost: ${total_cost:.4f}[/dim]")


def _read_topic(paths) -> str:
    if not paths.claude_md.exists():
        return ""
    text = paths.claude_md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# Vault Schema"):
            _, _, after = line.partition("—")
            return after.strip()
    return ""


_UNSUPPORTED_BINARY_EXTS = (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods")


def _prepare_local_source(
    paths,
    source_path: Path,
    *,
    eager_vision: bool = True,
    force: bool = False,
    force_ocr: bool = False,
    ocr_dpi: int = 180,
):
    """Route a local file to the right raw/ bucket before ingest.

    - `.pdf` → copy binary to raw/papers/ + extract text companion .md.
      When `eager_vision` is True, every extracted figure is transcribed
      via Claude Vision so code embedded in figures becomes text-visible.
    - `.md` / `.txt` / no-extension / other → return as-is (orchestrator
      handles the copy to raw/articles/)
    - `.docx` / `.xlsx` / etc. → warn + refuse (Phase 8 will add these)

    Consults the vault's ingest registry: when `force=False` (default)
    and the source's content hash matches a previously-ingested file,
    returns None with a message so the caller can skip the pipeline.
    Use `--force-reingest` to override.

    Returns the Path that should be fed to IngestOrchestrator (the .md
    in the case of a PDF), or None to abort / skip.
    """
    from compendium.vault import IngestRegistry, hash_file

    ext = source_path.suffix.lower()

    if not force:
        registry = IngestRegistry(paths)
        content_hash = hash_file(source_path)
        # Tombstone check: if the Curator culled this source, refuse to
        # re-ingest unless --force-reingest. Prevents the loop from
        # chasing back a file Tier-4 just got rid of.
        tomb = registry.is_tombstoned(hash=content_hash)
        if tomb is not None:
            console.print(
                f"[yellow]Source tombstoned[/yellow] as "
                f"[cyan]{tomb.get('source_id', '?')}[/cyan] on "
                f"{tomb.get('culled', '?')}. "
                f"Reason: {tomb.get('reason', '(none)')}. "
                "Pass [bold]--force-reingest[/bold] to override."
            )
            return None
        hit = registry.find_by_hash(content_hash)
        if hit is not None:
            console.print(
                f"[yellow]Already ingested[/yellow] as "
                f"[cyan]{hit.source_id}[/cyan] on {hit.ingested}. "
                "Nothing to do. (Pass [bold]--force-reingest[/bold] to "
                "re-run the pipeline on a fresh copy.)"
            )
            return None

    if ext == ".pdf":
        return _prepare_local_pdf(
            paths,
            source_path,
            eager_vision=eager_vision,
            force_ocr=force_ocr,
            ocr_dpi=ocr_dpi,
        )
    if ext in _UNSUPPORTED_BINARY_EXTS:
        console.print(
            f"[red]Ingest of `{ext}` files is not yet supported.[/red] "
            f"For now, convert `{source_path.name}` to `.md` or `.pdf` and "
            f"re-run. (DOCX/XLSX native ingest is on the Phase 8 list.)"
        )
        return None
    return source_path


def _prepare_local_pdf(
    paths,
    pdf_path: Path,
    *,
    eager_vision: bool = True,
    force_ocr: bool = False,
    ocr_dpi: int = 180,
):
    """Copy a local PDF to raw/papers/ and extract text + figures alongside.

    Text comes out page-by-page with `## [page N]` headings. Figure-heavy
    pages (those where text extraction returned <150 chars) are rendered
    as PNGs into `raw/assets/<slug>/`, and embedded image resources are
    extracted the same way.

    When `eager_vision=True` (default), every extracted figure is sent
    to Claude Vision at ingest time; the returned description (with any
    code inside the figure transcribed verbatim as fenced blocks) is
    spliced into the source .md next to the image reference. This
    makes figure content — crucially, any code embedded as bitmap
    inside an architecture diagram — visible to the downstream
    text-only Router and PageWriter, which otherwise only see prose.

    `force_ocr=True` switches the extractor to vision-only mode:
    every page is rendered at `ocr_dpi` (default 180 — high enough
    for body-text OCR) and sent to Claude Vision with a dedicated
    OCR prompt. Any text extraction PyMuPDF produced is discarded.
    Use for scanned books, PDFs with broken font cmaps, or image-only
    publications. Cost scales linearly with page count (~$0.01-$0.03
    per page at synthesis tier, less at mechanical).

    Falls back to pypdf-only text extraction if PyMuPDF isn't available.
    """
    import shutil
    from compendium.config import CompendiumConfig
    from compendium.guardrails.cost_tracker import CostTracker
    from compendium.sources.pdf_image_extractor import (
        OCR_PAGE_PROMPT,
        describe_pdf_figures,
        extract_pdf_with_figures,
        render_pages_as_markdown,
        summarize_extraction,
    )
    from compendium.vault import slugify, today_iso

    paths.raw_papers.mkdir(parents=True, exist_ok=True)

    # Common case: user drops a PDF directly into raw/papers/ (big files
    # especially). Don't copy-to-self and create duplicate `-2` slugs —
    # use it in place and pick the companion .md slug from the existing
    # filename.
    already_in_raw = False
    try:
        pdf_path.resolve().relative_to(paths.raw_papers.resolve())
        already_in_raw = True
    except ValueError:
        already_in_raw = False

    base_slug = slugify(pdf_path.stem) or "local-pdf"
    if already_in_raw:
        slug = pdf_path.stem
        target_pdf = pdf_path
    else:
        slug = base_slug
        n = 2
        while (paths.raw_papers / f"{slug}.pdf").exists() or (
            paths.raw_papers / f"{slug}.md"
        ).exists():
            slug = f"{base_slug}-{n}"
            n += 1
        target_pdf = paths.raw_papers / f"{slug}.pdf"
        try:
            shutil.copy2(pdf_path, target_pdf)
        except Exception as exc:
            console.print(f"[red]Failed to copy PDF: {exc}[/red]")
            return None

    # Record this ingest in the registry so future runs can detect duplicates.
    try:
        from compendium.vault import IngestRegistry, RegistryEntry, hash_file, now_iso

        IngestRegistry(paths).record(
            RegistryEntry(
                source_id=slug,
                raw_path=target_pdf.relative_to(paths.root).as_posix(),
                ingested=now_iso(),
                hash=hash_file(target_pdf),
                origin=str(pdf_path),
            )
        )
    except Exception:
        # Registry is advisory — never fail the ingest over it.
        pass

    title = _pdf_title(target_pdf, fallback_stem=pdf_path.stem)

    # Pull text + figures with PyMuPDF (preferred path).
    assets_dir = paths.raw_assets / slug
    pages = extract_pdf_with_figures(
        target_pdf,
        assets_dir,
        dpi=ocr_dpi if force_ocr else 120,
        force_render_all=force_ocr,
        drop_text=force_ocr,
    )

    descriptions: dict[str, str] = {}
    vision_cost = 0.0
    if pages and (eager_vision or force_ocr):
        config = CompendiumConfig()
        vision_provider_name = (
            getattr(config, "vision_provider", "anthropic") or "anthropic"
        ).lower()
        needs_auth = vision_provider_name == "anthropic"
        if config.has_auth or not needs_auth:
            from compendium.sources.vision_provider import make_vision_provider

            client = config.create_anthropic_client() if config.has_auth else None
            vision_tracker = CostTracker(config.get_guardrails())
            # OCR mode needs the synthesis tier for readable transcripts —
            # mechanical misses small text, italics, marginalia. Figure-
            # describe mode stays on mechanical for cost. Only applies to
            # the Anthropic provider; Ollama uses the configured vision_model.
            vision_model = (
                config.tiered_models.synthesis
                if force_ocr
                else config.tiered_models.mechanical
            ) if vision_provider_name == "anthropic" else None
            vision_provider = make_vision_provider(
                config=config,
                client=client,
                cost_tracker=vision_tracker,
                model_override=vision_model,
            )
            total_targets = sum(1 for p in pages if p.rendered_image) + (
                0 if force_ocr
                else sum(len(p.embedded_images) for p in pages)
            )
            mode_label = "OCR'ing pages" if force_ocr else "transcribing figures"
            backend_label = (
                f"Gemma via Ollama ({vision_provider.model})"
                if vision_provider_name == "ollama"
                else "Claude Vision"
            )
            console.print(
                f"  [cyan]{mode_label} via {backend_label}[/cyan] "
                f"({total_targets} page(s))..."
            )
            descriptions = describe_pdf_figures(
                pages,
                provider=vision_provider,
                max_figures=None if force_ocr else 40,
                prompt=OCR_PAGE_PROMPT if force_ocr else None,
                only_rendered_pages=force_ocr,
            )
            vision_cost = vision_tracker.get_summary()["estimated_cost_usd"]
            transcribed_label = "OCR'd" if force_ocr else "transcribed"
            console.print(
                f"  [green]{transcribed_label}[/green] {len(descriptions)} "
                f"page(s) [dim](${vision_cost:.3f})[/dim]"
            )
        else:
            console.print(
                "[yellow]Skipping eager Vision transcription — no API auth "
                "configured.[/yellow]"
            )

    if pages:
        extracted = render_pages_as_markdown(
            pages,
            assets_rel_base=f"../assets/{slug}",
            figure_descriptions=descriptions,
        )
        stats = summarize_extraction(pages)
        stats["figures_described"] = len(descriptions)
        stats["vision_cost"] = vision_cost
    else:
        # Fallback to pypdf text-only if PyMuPDF isn't available
        from compendium.sources.pdf_fetcher import extract_pdf_text

        extracted = extract_pdf_text(target_pdf)
        stats = {
            "pages": 0,
            "text_chars": len(extracted),
            "rendered_pages": 0,
            "embedded_figures": 0,
            "figures_described": 0,
            "vision_cost": 0.0,
        }

    if not extracted.strip():
        console.print(
            f"[yellow]Warning:[/yellow] no text or figures extracted from "
            f"[cyan]{pdf_path.name}[/cyan]. (Possible cause: scanned-image "
            "PDF requiring OCR — not yet supported.)"
        )

    body_parts = [
        f"<!-- source: {pdf_path.as_posix()} -->",
        f"<!-- source_type: local-pdf -->",
        f"<!-- ingested: {today_iso()} -->",
        "",
        f"# {title}",
        "",
        f"Local PDF ingested from `{pdf_path.name}`. The binary is "
        f"archived at `raw/papers/{slug}.pdf`.",
        "",
    ]
    extras: list[str] = []
    if stats["rendered_pages"]:
        extras.append(
            f"{stats['rendered_pages']} figure-heavy page"
            f"{'s' if stats['rendered_pages'] != 1 else ''} rendered to "
            f"`raw/assets/{slug}/`"
        )
    if stats["embedded_figures"]:
        extras.append(
            f"{stats['embedded_figures']} embedded image"
            f"{'s' if stats['embedded_figures'] != 1 else ''} extracted"
        )
    if extras:
        body_parts.append("Figures: " + "; ".join(extras) + ".")
        body_parts.append("")

    if extracted:
        body_parts.extend(["## Extracted content", "", extracted])

    md_path = paths.raw_papers / f"{slug}.md"
    md_path.write_text("\n".join(body_parts) + "\n", encoding="utf-8", newline="\n")

    console.print(f"[green]PDF archived:[/green] {target_pdf}")
    console.print(
        f"[green]Extracted[/green] {stats['text_chars']:,} chars of text "
        f"across {stats['pages']} pages → {md_path}"
    )
    if stats["rendered_pages"] or stats["embedded_figures"]:
        console.print(
            f"  [cyan]figures:[/cyan] "
            f"{stats['rendered_pages']} rendered page"
            f"{'s' if stats['rendered_pages'] != 1 else ''}, "
            f"{stats['embedded_figures']} embedded"
            f"{'s' if stats['embedded_figures'] != 1 else ''} → "
            f"[cyan]raw/assets/{slug}/[/cyan]"
        )
    return md_path


def _pdf_title(pdf_path: Path, *, fallback_stem: str) -> str:
    """Pull /Title from PDF metadata if present; fall back to the filename."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        meta = getattr(reader, "metadata", None)
        if meta:
            raw = meta.get("/Title") or ""
            if raw:
                t = str(raw).strip()
                if t and len(t) > 3:
                    return t
    except Exception:
        pass
    return fallback_stem.replace("_", " ").replace("-", " ").strip().title()


def _fetch_url_to_raw(paths, url: str, *, force: bool = False):
    """Fetch a URL as markdown and save it to raw/articles/ before ingest.

    When `force=False` (default), consults the ingest registry first;
    a prior ingest of the same URL returns None with a friendly message
    so the caller can skip. `force=True` refetches into a suffixed slug.

    Returns the Path of the saved file, None on dedup-skip, or None
    on failure.
    """
    from urllib.parse import urlparse
    from compendium.config import CompendiumConfig
    from compendium.repl.functions import make_fetch_url_fn
    from compendium.vault import IngestRegistry, RegistryEntry, hash_file, now_iso, slugify

    if not force:
        registry = IngestRegistry(paths)
        tomb = registry.is_tombstoned(url=url)
        if tomb is not None:
            console.print(
                f"[yellow]URL tombstoned[/yellow] (culled as "
                f"[cyan]{tomb.get('source_id', '?')}[/cyan] on "
                f"{tomb.get('culled', '?')}). "
                f"Reason: {tomb.get('reason', '(none)')}. "
                "Pass [bold]--force-reingest[/bold] to override."
            )
            return None
        hit = registry.find_by_url(url)
        if hit is not None:
            console.print(
                f"[yellow]URL already ingested[/yellow] as "
                f"[cyan]{hit.source_id}[/cyan] on {hit.ingested}. "
                "Nothing to do. (Pass [bold]--force-reingest[/bold] to "
                "re-run the pipeline against a fresh fetch.)"
            )
            return None

    parsed = urlparse(url)
    # Derive a source_id from the URL path's last meaningful segment
    path_parts = [p for p in parsed.path.split("/") if p]
    seed = path_parts[-1] if path_parts else parsed.netloc
    source_id = slugify(seed) or slugify(parsed.netloc) or "url-source"

    console.print(f"[cyan]Fetching[/cyan] {url}")
    config = CompendiumConfig()
    fetch = make_fetch_url_fn(
        max_chars=200_000, jina_api_key=config.jina_api_key
    )
    markdown = fetch(url)
    if markdown.startswith("[FETCH ERROR]"):
        console.print(f"[red]{markdown}[/red]")
        return None

    paths.raw_articles.mkdir(parents=True, exist_ok=True)
    target = paths.raw_articles / f"{source_id}.md"
    # If collision, append a numeric suffix
    n = 2
    while target.exists():
        target = paths.raw_articles / f"{source_id}-{n}.md"
        n += 1
    # Prepend a note so the raw file records provenance
    header = f"<!-- source: {url} -->\n<!-- fetched: {__import__('datetime').date.today().isoformat()} -->\n\n"
    target.write_text(header + markdown, encoding="utf-8", newline="\n")
    console.print(f"[green]Saved[/green] {target}")

    # Record this URL ingest in the registry.
    try:
        IngestRegistry(paths).record(
            RegistryEntry(
                source_id=target.stem,
                raw_path=target.relative_to(paths.root).as_posix(),
                ingested=now_iso(),
                hash=hash_file(target),
                url=url,
                origin=url,
            )
        )
    except Exception:
        pass

    return target


def cmd_research(args: argparse.Namespace) -> None:
    """Research a topic on the open web → write sources to raw/ → auto-ingest each."""
    from compendium.agents.ingest_orchestrator import (
        IngestOrchestrator,
        VaultNotInitialized,
    )
    from compendium.agents.research_agent import ResearchAgent
    from compendium.config import CompendiumConfig
    from compendium.models import ReviewSeverity
    from compendium.vault import VaultPaths, append_entry

    paths = VaultPaths.for_vault(args.vault)
    if not paths.is_initialized():
        console.print(
            f"[red]Vault at {paths.root} is not initialized[/red] "
            "(no CLAUDE.md). Run `compendium init` first."
        )
        sys.exit(1)

    config = CompendiumConfig()
    if args.max_cost is not None:
        config.max_cost_dollars = args.max_cost
    if args.model_strategic:
        config.model_strategic = args.model_strategic
    if args.model_synthesis:
        config.model_synthesis = args.model_synthesis
    if args.model_mechanical:
        config.model_mechanical = args.model_mechanical
    if not config.has_auth:
        console.print(
            "[red]No API key found.[/red] Set ANTHROPIC_API_KEY in env or .env."
        )
        sys.exit(1)
    # web_search falls back to Jina Reader whenever JINA_API_KEY is set, even if
    # no explicit search_provider is configured — so don't block in that case.
    has_jina = bool(getattr(config, "jina_api_key", None))
    if config.search_provider == "none" and not has_jina:
        console.print(
            "[yellow]Warning: no search provider configured.[/yellow] "
            "Research requires web_search — set COMPENDIUM_SEARCH_PROVIDER "
            "(tavily, brave, or jina) and the matching key "
            "(COMPENDIUM_SEARCH_API_KEY, or JINA_API_KEY for jina)."
        )
        if not args.allow_no_search:
            sys.exit(1)

    try:
        orch = IngestOrchestrator(config, paths)
    except VaultNotInitialized as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    # Share cost tracker between research + ingest passes so budget is unified.
    researcher = ResearchAgent(
        client=orch.client,
        config=config,
        cost_tracker=orch.cost_tracker,
        vault=paths,
        tiered=config.tiered_models,
    )

    console.print(f"[cyan]Researching[/cyan] '[bold]{args.topic}[/bold]'")
    try:
        result = researcher.research(args.topic)
    except Exception as exc:
        console.print(f"[red]Research failed: {exc}[/red]")
        sys.exit(1)

    if not result.sources:
        console.print("[yellow]Research produced no sources.[/yellow]")
        sys.exit(1)

    console.print(
        f"\n[bold green]{len(result.sources)} source{'s' if len(result.sources) != 1 else ''} "
        f"written to raw/articles/:[/bold green]"
    )
    for src, path in zip(result.sources, result.raw_paths):
        console.print(f"  - [cyan]{path.name}[/cyan] — {src.title}")
    if result.notes:
        console.print(f"[dim]notes: {result.notes}[/dim]")

    # Log the research step itself so the history reads cleanly.
    log_body = [f"- topic: {args.topic}"]
    for src, path in zip(result.sources, result.raw_paths):
        log_body.append(f"- wrote: `{path.as_posix()}` — {src.title}")
    if result.notes:
        log_body.append(f"- notes: {result.notes}")
    log_body.append(
        f"- cost (research only): "
        f"${orch.cost_tracker.get_summary()['estimated_cost_usd']:.4f}"
    )
    append_entry(paths, op="research", subject=args.topic[:80], body="\n".join(log_body))

    if args.no_ingest:
        console.print(
            "\n[yellow]--no-ingest set; sources were written but not ingested.[/yellow]"
        )
        return

    # Auto-ingest each produced source. Skip auto-explore per source since
    # we'd otherwise run N explores; we'll run one consolidated explore after.
    console.print(
        f"\n[cyan]Ingesting[/cyan] {len(result.raw_paths)} source"
        f"{'s' if len(result.raw_paths) != 1 else ''}..."
    )
    total_created = 0
    total_updated = 0
    total_issues = 0
    for path in result.raw_paths:
        try:
            report = orch.ingest(path, run_explore=False)
        except Exception as exc:
            console.print(f"  [red]FAILED[/red] {path.name}: {exc}")
            continue
        total_created += len(report.pages_created)
        total_updated += len(report.pages_updated)
        total_issues += sum(
            1 for i in report.review_issues if i.severity != ReviewSeverity.INFO
        )
        console.print(
            f"  [green]✓[/green] {path.name} → "
            f"{len(report.pages_created)} created · "
            f"{len(report.pages_updated)} updated"
        )

    # One consolidated explore after all ingests
    try:
        exp_report = orch._explorer.explore()
        console.print(
            f"\n[green]explore[/green]: {len(exp_report.proposals)} proposals → "
            f"[cyan]wiki/_meta/expansion.md[/cyan]"
        )
    except Exception as exc:
        console.print(f"\n[yellow]explore failed: {exc}[/yellow]")

    total_cost = orch.cost_tracker.get_summary()["estimated_cost_usd"]
    console.print(
        f"\n[bold]totals:[/bold] "
        f"[green]{total_created}[/green] created · "
        f"[yellow]{total_updated}[/yellow] updated · "
        f"[red]{total_issues}[/red] review issues"
    )
    console.print(f"[dim]Cost: ${total_cost:.4f}[/dim]")


def cmd_loop(args: argparse.Namespace) -> None:
    """The autonomous research loop: research → ingest → (lint) → explore → select → repeat."""
    from collections import deque

    from compendium.agents.explorer import Explorer
    from compendium.agents.ingest_orchestrator import (
        IngestOrchestrator,
        VaultNotInitialized,
    )
    from compendium.agents.linter import Linter
    from compendium.agents.research_agent import ResearchAgent
    from compendium.config import CompendiumConfig
    from compendium.guardrails.cost_tracker import BudgetExceeded
    from compendium.models import ExpansionKind, ExpansionProposal
    from compendium.vault import VaultPaths, append_entry
    from compendium.vault.loop_state import LoopSession, load as load_loop_state

    paths = VaultPaths.for_vault(args.vault)
    if not paths.is_initialized():
        console.print(
            f"[red]Vault at {paths.root} is not initialized[/red] "
            "(no CLAUDE.md). Run `compendium init` first."
        )
        sys.exit(1)

    config = CompendiumConfig()
    if args.max_cost is not None:
        config.max_cost_dollars = args.max_cost
    if args.model_strategic:
        config.model_strategic = args.model_strategic
    if args.model_synthesis:
        config.model_synthesis = args.model_synthesis
    if args.model_mechanical:
        config.model_mechanical = args.model_mechanical
    if not config.has_auth:
        console.print(
            "[red]No API key found.[/red] Set ANTHROPIC_API_KEY in env or .env."
        )
        sys.exit(1)
    if config.search_provider == "none" and not config.jina_api_key:
        console.print(
            "[red]`loop` requires a search provider[/red] "
            "(set COMPENDIUM_SEARCH_PROVIDER=tavily|brave + "
            "COMPENDIUM_SEARCH_API_KEY, or a JINA_API_KEY). The loop can't research without it."
        )
        sys.exit(1)

    try:
        orch = IngestOrchestrator(config, paths)
    except VaultNotInitialized as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    # All agents share a single cost tracker so --max-cost is a genuine total cap.
    cost_tracker = orch.cost_tracker
    researcher = ResearchAgent(
        client=orch.client, config=config, cost_tracker=cost_tracker,
        vault=paths, tiered=config.tiered_models,
    )
    explorer = Explorer(orch.client, config, cost_tracker, paths)
    linter = Linter(orch.client, config, cost_tracker, paths)

    interactive = args.interactive
    auto_n = args.auto
    max_iters = args.max_iterations
    include_lint = not args.no_lint

    # Decide seed vs. resume. `--resume` loads the persisted queue and
    # seen set from `<vault>/.loop-state.json` and continues where a
    # prior session left off. Without --resume, a seed topic is required.
    persisted = load_loop_state(paths)
    resume = args.resume
    seed_topic = args.topic or ""
    if not resume and not seed_topic:
        console.print(
            "[red]Pass a seed topic OR use [bold]--resume[/bold].[/red] "
            f"({len(persisted.queue)} pending proposal"
            f"{'s' if len(persisted.queue) != 1 else ''} in persisted state.)"
        )
        sys.exit(1)
    if resume and not persisted.queue:
        console.print(
            "[yellow]--resume given but persisted queue is empty.[/yellow] "
            "Run `explore` to refresh proposals, or pass a seed topic."
        )
        sys.exit(1)

    unlimited_iters = max_iters <= 0

    with LoopSession(paths, seed_topic=seed_topic) as sess:
        # Seed path: prepend the seed as a synthetic proposal (with
        # rationale + related pages) so the research agent receives it
        # with the same context shape as any Explorer proposal.
        if not resume:
            if _norm_topic(seed_topic) not in sess.state.seen:
                seed_proposal = ExpansionProposal(
                    kind=ExpansionKind.GAP,
                    title=seed_topic,
                    priority=1,
                    signal="user-provided seed topic (no mechanical signal)",
                    rationale=(
                        "Seed topic for this loop run. Treat as broad "
                        "orientation: survey the landscape, surface 2-5 "
                        "focused sources, and emphasize sources the vault's "
                        "existing index does not yet cover. Later iterations "
                        "will target specific expansion proposals."
                    ),
                    related=[],
                )
                sess.state.queue.insert(0, seed_proposal)
                sess.state.seen.add(_norm_topic(seed_topic))
                sess.save_snapshot()

        topic_queue: deque[ExpansionProposal] = deque(sess.state.queue)
        seen_topics: set[str] = set(sess.state.seen)

        _loop_banner(
            console,
            seed_topic or "(resume)",
            0 if unlimited_iters else max_iters,
            config.max_cost_dollars,
            interactive,
            cumulative_cost=sess.state.cumulative_cost(),
            cumulative_pages=sess.state.cumulative_pages(),
            queue_size=len(topic_queue),
        )

        # Surface backlog count on every resume so deferred pages don't
        # linger invisibly. Paying Writer cost to drain them is typically
        # cheaper than paying Router cost again to re-plan.
        try:
            from compendium.vault import PageBacklog as _PBL
            _bl_count = _PBL(paths).count()
            if _bl_count:
                console.print(
                    f"\n[yellow]backlog: {_bl_count} deferred page"
                    f"{'s' if _bl_count != 1 else ''}[/yellow] "
                    "— consider "
                    f"[cyan]cli.py flush-backlog --vault {paths.root}[/cyan] "
                    "before this iter (cheaper than research+ingest).\n"
                )
        except Exception:
            pass

        # Lifetime cost warning: the per-session budget is reset on every
        # --resume, so a topic can silently accumulate $60+ across sessions.
        # `COMPENDIUM_VAULT_LIFETIME_CAP=50` (or the YAML equivalent) raises
        # a clear warning once sum-of-sessions crosses the cap. Advisory,
        # not blocking — user can override by acknowledging.
        lifetime_cap = getattr(config, "vault_lifetime_cap", 0.0) or 0.0
        if lifetime_cap > 0:
            lifetime_spent = sess.state.cumulative_cost()
            projected = lifetime_spent + (config.max_cost_dollars or 0.0)
            if lifetime_spent >= lifetime_cap:
                console.print(
                    f"\n[bold red]⚠ lifetime cap reached[/bold red] "
                    f"(spent ${lifetime_spent:.2f} ≥ cap ${lifetime_cap:.2f}). "
                    f"Session will still run with its own budget cap — but "
                    "reconsider before another resume. Raise "
                    "COMPENDIUM_VAULT_LIFETIME_CAP to dismiss.\n"
                )
            elif projected >= lifetime_cap:
                console.print(
                    f"\n[yellow]⚠ this session will likely cross the lifetime "
                    f"cap[/yellow] (spent ${lifetime_spent:.2f} + budget "
                    f"${config.max_cost_dollars:.2f} = ~${projected:.2f} vs "
                    f"cap ${lifetime_cap:.2f})\n"
                )
        append_entry(
            paths,
            op="loop-resume" if resume else "loop-start",
            subject=(seed_topic or f"resume ({len(topic_queue)} pending)")[:80],
            body=(
                f"- seed topic: {seed_topic or '(resume)'}\n"
                f"- max iterations: {'unlimited' if unlimited_iters else max_iters}\n"
                f"- budget: ${config.max_cost_dollars:.2f}\n"
                f"- persisted queue at start: {len(topic_queue)}\n"
                f"- cumulative cost across sessions: "
                f"${sess.state.cumulative_cost():.4f}"
            ),
        )

        iteration = 0
        pages_created_total = 0
        try:
            while topic_queue and (unlimited_iters or iteration < max_iters):
                current_proposal = topic_queue.popleft()
                current_topic = current_proposal.title
                iteration += 1
                iters_label = "∞" if unlimited_iters else str(max_iters)
                console.rule(
                    f"[bold magenta]iter {iteration}/{iters_label} — "
                    f"[{current_proposal.kind.value} p{current_proposal.priority}] "
                    f"{current_topic}[/bold magenta]"
                )
                if (
                    current_proposal.rationale
                    and current_proposal.kind != ExpansionKind.GAP
                ):
                    console.print(
                        f"  [dim]rationale:[/dim] {current_proposal.rationale}"
                    )
                if current_proposal.related:
                    console.print(
                        f"  [dim]related pages:[/dim] "
                        + ", ".join(
                            f"[cyan]{r}[/cyan]"
                            for r in current_proposal.related[:6]
                        )
                    )

                expansion_doc_text = _read_expansion_doc(paths)

                # Research
                try:
                    research = researcher.research(
                        current_topic,
                        proposal=current_proposal,
                        expansion_doc_text=expansion_doc_text,
                    )
                except Exception as exc:
                    console.print(f"[red]research failed: {exc}[/red]")
                    sess.state.queue = list(topic_queue)
                    sess.state.seen = set(seen_topics)
                    sess.save_snapshot()
                    continue
                if not research.sources:
                    console.print(
                        "[yellow]research produced no sources; moving on.[/yellow]"
                    )
                    sess.state.queue = list(topic_queue)
                    sess.state.seen = set(seen_topics)
                    sess.save_snapshot()
                    continue
                console.print(
                    f"  [green]{len(research.sources)}[/green] source file"
                    f"{'s' if len(research.sources) != 1 else ''} written"
                )

                # Ingest each source
                for path in research.raw_paths:
                    try:
                        report = orch.ingest(path, run_explore=False)
                    except Exception as exc:
                        console.print(
                            f"  [red]ingest {path.name} failed: {exc}[/red]"
                        )
                        continue
                    pages_created_total += len(report.pages_created)
                    console.print(
                        f"    [dim]{path.name}: +{len(report.pages_created)} pages, "
                        f"~{len(report.pages_updated)} updates, "
                        f"${report.cost_dollars:.3f} total[/dim]"
                    )

                # Lint (skip on first iter when cumulative iters also == 1)
                lint_report_for_mend = None
                if include_lint and (
                    sess.iteration_count + iteration > 1 or resume
                ):
                    try:
                        lint_report_for_mend = linter.lint()
                        real_contradictions = [
                            c for c in lint_report_for_mend.contradictions if c.pages
                        ]
                        console.print(
                            f"  [dim]lint: {len(lint_report_for_mend.orphan_pages)} orphans, "
                            f"{len(real_contradictions)} contradictions, "
                            f"{lint_report_for_mend.citations_unverified} unverified "
                            f"citations[/dim]"
                        )
                    except Exception as exc:
                        console.print(f"  [yellow]lint failed: {exc}[/yellow]")

                # Mend (tier 1 + 2) — fix what Lint found AND produce
                # escalation signals for the Explorer call that follows.
                # Tier 3 stays out of the loop — it's PageWriter-cost per
                # page and would compound the ingest bill. Call the
                # standalone `mend` command for that.
                if lint_report_for_mend is not None:
                    try:
                        from compendium.agents.mender import MendConfig, mend_vault

                        mend_report = mend_vault(
                            client=orch.client,
                            config=config,
                            cost_tracker=cost_tracker,
                            vault=paths,
                            lint_report=lint_report_for_mend,
                            mend_config=MendConfig(
                                tiers={1, 2},
                                dry_run=False,
                                max_tier2_issues=20,
                            ),
                        )
                        if mend_report.actions:
                            console.print(
                                f"  [dim]mend: {len(mend_report.actions)} "
                                f"action{'s' if len(mend_report.actions) != 1 else ''}, "
                                f"${mend_report.cost_dollars:.3f}[/dim]"
                            )
                    except BudgetExceeded as exc:
                        console.print(
                            f"  [yellow]mend halted at budget: {exc}[/yellow]"
                        )
                        raise  # propagate so the loop's BudgetExceeded handler fires
                    except Exception as exc:
                        console.print(f"  [yellow]mend failed: {exc}[/yellow]")

                # Explore
                try:
                    exp_report = explorer.explore()
                except Exception as exc:
                    console.print(f"[yellow]explore failed: {exc}[/yellow]")
                    break

                # Select next proposals
                next_proposals = _select_next_topics(
                    exp_report,
                    interactive=interactive,
                    auto_n=auto_n,
                    seen=seen_topics,
                )
                for p in next_proposals:
                    topic_queue.append(p)
                    seen_topics.add(_norm_topic(p.title))
                if next_proposals:
                    console.print(
                        f"  [cyan]queued {len(next_proposals)} proposal"
                        f"{'s' if len(next_proposals) != 1 else ''} "
                        f"({len(topic_queue)} total pending):[/cyan]"
                    )
                    for p in next_proposals:
                        console.print(
                            f"    - [magenta]p{p.priority}[/magenta] "
                            f"[yellow]{p.kind.value}[/yellow] {p.title}"
                        )
                elif not topic_queue:
                    console.print(
                        "  [bold yellow]no more research-worthy "
                        "proposals; converged[/bold yellow]"
                    )

                # Snapshot state after every completed iteration so a
                # crash or stop doesn't lose pending work.
                sess.iteration_count = iteration
                sess.session_pages = pages_created_total
                sess.session_cost = cost_tracker.get_summary()["estimated_cost_usd"]
                sess.state.queue = list(topic_queue)
                sess.state.seen = set(seen_topics)
                # Bound the persisted queue so it doesn't grow unbounded
                # across many sessions — Explorer re-surfaces anything
                # still relevant on its next run anyway.
                dropped = sess.state.trim_queue()
                if dropped:
                    console.print(
                        f"  [dim]queue trimmed: dropped {dropped} lower-"
                        f"priority proposal{'s' if dropped != 1 else ''} "
                        f"(cap {len(sess.state.queue)})[/dim]"
                    )
                    # Reflect trim into the live deque so next iter doesn't
                    # pull from items already dropped.
                    topic_queue = deque(sess.state.queue)
                sess.save_snapshot()

                if not topic_queue:
                    sess.terminated_by = "convergence"
                    break

            else:
                # Hit the iter cap with queue still non-empty
                if not unlimited_iters and topic_queue:
                    sess.terminated_by = "iters_cap"

        except BudgetExceeded as exc:
            console.print(f"\n[red]Budget exceeded: {exc}[/red]")
            sess.terminated_by = "budget"
        except KeyboardInterrupt:
            console.print("\n[yellow]Loop interrupted by user.[/yellow]")
            sess.terminated_by = "signal"
            raise  # let LoopSession's __exit__ save before re-raising

        # Guaranteed end-of-session explore: expansion.md is the
        # "what's next" snapshot for this vault. If the per-iteration
        # explore failed (budget exhaust, convergence-before-explore,
        # exception) the document is stale — still proposing things
        # we just built. A dedicated budget reserve lets this pass
        # run even after BudgetExceeded fires on the main tracker.
        _run_final_explore_if_stale(
            explorer=explorer,
            paths=paths,
            cost_tracker=cost_tracker,
            session_started=sess._started,
            console=console,
            reserve_dollars=0.50,
        )

        # Finalize session counters
        sess.iteration_count = iteration
        sess.session_pages = pages_created_total
        sess.session_cost = cost_tracker.get_summary()["estimated_cost_usd"]
        sess.state.queue = list(topic_queue)
        sess.state.seen = set(seen_topics)
        sess.save_snapshot()

        session_cost = sess.session_cost
        cumulative_after = sess.state.cumulative_cost() + session_cost  # session record not yet appended
        console.rule(
            f"[bold green]loop session complete — {iteration} iteration(s)[/bold green]"
        )
        console.print(
            f"  pages created this session: [green]{pages_created_total}[/green] · "
            f"session cost: [bold]${session_cost:.4f}[/bold]"
        )
        console.print(
            f"  queue remaining: [yellow]{len(topic_queue)}[/yellow] · "
            f"total across all sessions: "
            f"[bold]${cumulative_after:.4f}[/bold] / "
            f"{sess.state.cumulative_pages() + pages_created_total} pages / "
            f"{sess.state.cumulative_iterations() + iteration} iterations"
        )
        if topic_queue:
            console.print(
                "  [dim]resume later with[/dim] "
                f"[cyan]python cli.py loop --resume --vault {paths.root}[/cyan]"
            )
        append_entry(
            paths, op="loop-end", subject=(seed_topic or "resume")[:80],
            body=(
                f"- iterations this session: {iteration}\n"
                f"- pages this session: {pages_created_total}\n"
                f"- session cost: ${session_cost:.4f}\n"
                f"- queue remaining: {len(topic_queue)}\n"
                f"- terminated_by: {sess.terminated_by}"
            ),
        )


def _run_final_explore_if_stale(
    *,
    explorer,
    paths,
    cost_tracker,
    session_started: str,
    console,
    reserve_dollars: float = 0.50,
) -> None:
    """Re-run explore at session end if expansion.md wasn't refreshed.

    The per-iteration explore can fail (budget exhaust, exception) and
    silently leave `wiki/_meta/expansion.md` pre-dating the new pages.
    That makes the vault's "what's next" snapshot wrong until the next
    session. This function detects the stale case (missing file, or
    mtime < session-start) and runs one more explore with a temporary
    `reserve_dollars` budget bump so it can execute even after the
    main budget was exceeded.

    Best-effort: if the fallback explore also fails (e.g., API outage),
    we log and continue — the session still ends cleanly.
    """
    from datetime import datetime
    try:
        session_start_dt = datetime.fromisoformat(session_started)
    except ValueError:
        session_start_dt = None
    expansion_md = paths.meta / "expansion.md"

    needs_refresh = True
    if expansion_md.exists() and session_start_dt is not None:
        mtime = datetime.fromtimestamp(expansion_md.stat().st_mtime)
        if mtime > session_start_dt:
            needs_refresh = False

    if not needs_refresh:
        return

    # Temporarily lift the budget ceiling so explore can complete even
    # if the main work already hit BudgetExceeded. We mutate on the
    # guardrails dataclass directly; CostTracker re-reads `max_cost_dollars`
    # via `self.guardrails.max_cost_dollars` on every check.
    gr = cost_tracker.guardrails
    original_max = gr.max_cost_dollars
    current_spend = cost_tracker.get_summary()["estimated_cost_usd"]
    gr.max_cost_dollars = current_spend + reserve_dollars
    try:
        console.print(
            "  [dim]expansion.md stale — running final explore "
            f"(reserve ${reserve_dollars:.2f})[/dim]"
        )
        exp_report = explorer.explore()
        console.print(
            f"  [green]explore[/green]: {len(exp_report.proposals)} "
            f"proposals → [cyan]wiki/_meta/expansion.md[/cyan]"
        )
    except Exception as exc:
        console.print(f"  [yellow]final explore failed: {exc}[/yellow]")
    finally:
        gr.max_cost_dollars = original_max


def _norm_topic(t: str) -> str:
    return " ".join(t.split()).lower()


def _loop_banner(
    console,
    topic: str,
    iters: int,
    budget: float,
    interactive: bool,
    *,
    cumulative_cost: float = 0.0,
    cumulative_pages: int = 0,
    queue_size: int = 0,
) -> None:
    mode = "interactive" if interactive else "auto"
    iters_label = "unlimited" if iters == 0 else str(iters)
    parts = [
        f"[bold cyan]loop[/bold cyan] — seed: [bold]{topic}[/bold]",
        f"iters: {iters_label}",
        f"budget: ${budget:.2f}",
        f"queue: {queue_size}",
        f"selection: {mode}",
    ]
    console.print(" · ".join(parts))
    if cumulative_cost > 0 or cumulative_pages > 0:
        console.print(
            f"  [dim]lifetime across sessions: "
            f"${cumulative_cost:.4f} / {cumulative_pages} pages[/dim]"
        )


def _read_expansion_doc(paths) -> str:
    """Return the current _meta/expansion.md content, or empty string."""
    try:
        if paths.expansion_md.exists():
            return paths.expansion_md.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _select_next_topics(
    exp_report, *, interactive: bool, auto_n: int, seen: set[str]
):
    """Pick the next iteration's proposals from Explorer output.

    Returns full ExpansionProposal objects (not title strings) so the
    research agent gets the signal, rationale, and related-pages
    context — not just a topic string.

    Only three categories are research-worthy: gap, source_suggestion,
    open_question. The others (missed_connection, thesis_drift) are
    editorial and shouldn't trigger new research.
    """
    from compendium.models import ExpansionKind

    research_kinds = {
        ExpansionKind.GAP,
        ExpansionKind.SOURCE_SUGGESTION,
        ExpansionKind.OPEN_QUESTION,
    }
    candidates = [
        p for p in exp_report.proposals
        if p.kind in research_kinds
        and _norm_topic(p.title) not in seen
    ]
    candidates.sort(key=lambda p: (p.priority, p.title))
    if not candidates:
        return []

    if interactive:
        console.print("\n[bold]Select proposals to research next[/bold] "
                      "(comma-separated numbers, empty to stop):")
        for i, p in enumerate(candidates[:15], start=1):
            console.print(
                f"  [magenta]{i:2d}.[/magenta] "
                f"[yellow]p{p.priority}[/yellow] "
                f"[cyan]{p.kind.value}[/cyan] {p.title}"
            )
        raw = input("> ").strip()
        if not raw:
            return []
        chosen = []
        for part in raw.split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            idx = int(part) - 1
            if 0 <= idx < len(candidates):
                chosen.append(candidates[idx])
        return chosen

    return candidates[:auto_n]


def cmd_split_book(args: argparse.Namespace) -> None:
    """Split a long PDF into chapter-sized markdown chunks in `raw/articles/`.

    Preprocessing-only — no LLM calls against Router / PageWriter /
    Reviewer. The produced files are normal sources that subsequent
    `ingest` or `loop` commands pick up at your chosen pace.
    """
    from compendium.config import CompendiumConfig
    from compendium.guardrails.cost_tracker import CostTracker
    from compendium.sources.book_splitter import (
        fill_chunks_native,
        fill_chunks_ocr,
        plan_split,
        write_chunks,
    )
    from compendium.sources.vision_provider import make_vision_provider
    from compendium.vault.layout import VaultPaths
    from compendium.vault.registry import hash_file

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        console.print(f"[red]Not a PDF: {pdf_path}[/red]")
        sys.exit(1)

    vault = Path(args.vault).resolve()
    if not vault.is_dir():
        console.print(f"[red]Vault directory doesn't exist: {vault}[/red]")
        sys.exit(1)
    paths = VaultPaths(vault)

    # Plan — decide TOC vs fixed, list chunks, total pages.
    strategy = "toc" if args.chapters_from_toc else (
        "fixed" if args.pages_per_chunk else "auto"
    )
    try:
        plan = plan_split(
            pdf_path,
            strategy=strategy,
            toc_max_level=args.toc_depth,
            pages_per_chunk=args.pages_per_chunk or 25,
            min_pages_per_chunk=args.min_pages_per_chunk,
            book_title_override=args.title,
        )
    except Exception as exc:
        console.print(f"[red]Plan failed:[/red] {exc}")
        sys.exit(1)

    total_pages = sum(
        c.page_end - c.page_start + 1 for c in plan.chunks
    )
    console.print(
        f"[cyan]Book:[/cyan] {plan.book_title}  "
        f"[dim]({plan.book_slug})[/dim]"
    )
    console.print(
        f"[cyan]Strategy:[/cyan] {plan.strategy}"
        + (f" (TOC max level {plan.toc_max_level})" if plan.strategy == "toc"
           else f" ({plan.pages_per_chunk} pages/chunk)")
    )
    console.print(
        f"[cyan]Chunks:[/cyan] {len(plan.chunks)}  "
        f"[cyan]Pages:[/cyan] {total_pages}"
    )
    if args.dry_run:
        for c in plan.chunks[:20]:
            console.print(
                f"  {c.index:3d}. [dim]pp{c.page_start:>4}-{c.page_end:<4}[/dim] "
                f"{c.title[:80]}"
            )
        if len(plan.chunks) > 20:
            console.print(f"  ... and {len(plan.chunks) - 20} more")
        console.print(
            "\n[yellow]--dry-run[/yellow]: nothing written. "
            "Drop the flag to write chunks to raw/articles/."
        )
        return

    # Extract text — native or OCR.
    if args.ocr:
        config = CompendiumConfig()
        vision_provider_name = (
            getattr(config, "vision_provider", "anthropic") or "anthropic"
        ).lower()
        needs_auth = vision_provider_name == "anthropic"
        if needs_auth and not config.has_auth:
            console.print(
                "[red]--ocr requested but no Anthropic auth configured.[/red] "
                "Either set ANTHROPIC_API_KEY or switch to "
                "COMPENDIUM_VISION_PROVIDER=ollama."
            )
            sys.exit(1)
        client = config.create_anthropic_client() if config.has_auth else None
        tracker = CostTracker(config.get_guardrails())
        provider = make_vision_provider(
            config=config,
            client=client,
            cost_tracker=tracker,
        )
        backend = (
            f"Gemma via Ollama ({provider.model})"
            if vision_provider_name == "ollama"
            else f"Claude Vision ({provider.model})"
        )
        console.print(
            f"  [cyan]OCR'ing {total_pages} pages via {backend}[/cyan] "
            f"(this can take a while)..."
        )
        assets_dir = paths.raw_assets / f"{plan.book_slug}-ocr" if args.save_renders else None

        def _progress(done: int, total: int) -> None:
            if done == total or done % max(1, total // 20) == 0:
                console.print(
                    f"    [dim]OCR progress: {done}/{total}[/dim]"
                )
        fill_chunks_ocr(
            pdf_path,
            plan.chunks,
            provider,
            dpi=args.ocr_dpi,
            max_workers=args.workers,
            assets_dir=assets_dir,
            progress_cb=_progress,
        )
        spent = tracker.get_summary()["estimated_cost_usd"]
        console.print(f"  [green]OCR done[/green] [dim](${spent:.3f})[/dim]")
    else:
        console.print(f"  [cyan]Extracting text natively for {total_pages} pages[/cyan]")
        fill_chunks_native(pdf_path, plan.chunks)

    # Write chunks.
    try:
        source_hash = hash_file(pdf_path)
    except Exception:
        source_hash = ""
    out_dir = paths.raw_articles
    written = write_chunks(
        plan,
        pdf_path,
        out_dir,
        source_hash=source_hash,
        overwrite=args.overwrite,
    )
    empty_count = sum(1 for c in plan.chunks if not c.text.strip())
    console.print(
        f"[green]Wrote[/green] {len(written)} chunk(s) to "
        f"{out_dir.relative_to(vault).as_posix()}"
    )
    if empty_count:
        console.print(
            f"[yellow]Warning:[/yellow] {empty_count} chunk(s) have no "
            f"extracted text. Consider re-running with --ocr if this is "
            f"a scanned book."
        )
    console.print(
        f"\nNext: [cyan]python cli.py ingest[/cyan] each chunk, or use "
        f"[cyan]loop --resume[/cyan] to let the agent pick them up."
    )


def cmd_explore(args: argparse.Namespace) -> None:
    """Generate expansion proposals: gaps, open questions, source suggestions."""
    from compendium.agents.explorer import Explorer
    from compendium.config import CompendiumConfig
    from compendium.guardrails.cost_tracker import CostTracker
    from compendium.vault import VaultPaths

    paths = VaultPaths.for_vault(args.vault)
    if not paths.is_initialized():
        console.print(
            f"[red]Vault at {paths.root} is not initialized[/red] "
            "(no CLAUDE.md). Run `compendium init` first."
        )
        sys.exit(1)

    config = CompendiumConfig()
    if args.max_cost is not None:
        config.max_cost_dollars = args.max_cost
    if args.model_strategic:
        config.model_strategic = args.model_strategic

    if not config.has_auth:
        console.print(
            "[red]No API key found.[/red] Set ANTHROPIC_API_KEY in env or .env."
        )
        sys.exit(1)

    client = config.create_anthropic_client()
    cost_tracker = CostTracker(config.get_guardrails())
    explorer = Explorer(client, config, cost_tracker, paths)

    console.print(f"[cyan]Exploring[/cyan] {paths.root}")
    try:
        report = explorer.explore()
    except Exception as exc:
        console.print(f"[red]Explore failed: {exc}[/red]")
        sys.exit(1)

    cost = cost_tracker.get_summary()["estimated_cost_usd"]
    if not report.proposals:
        console.print(
            "[yellow]No proposals generated.[/yellow] "
            "(Vault may be too sparse — ingest a source or two first.)"
        )
        console.print(f"[dim]Cost: ${cost:.4f}[/dim]")
        return

    console.print(
        f"\n[bold green]{len(report.proposals)} proposals[/bold green] "
        f"→ [cyan]{paths.expansion_md}[/cyan]"
    )
    # Compact per-category counts
    from collections import Counter

    counts = Counter(p.kind.value for p in report.proposals)
    for kind in ("gap", "open_question", "missed_connection", "source_suggestion", "thesis_drift"):
        if counts.get(kind):
            console.print(f"  - {kind}: {counts[kind]}")

    # Sync fresh proposals into the persisted loop queue so a later
    # `loop --resume` picks them up. No-op if no loop state exists.
    from compendium.vault import sync_proposals_to_queue

    added = sync_proposals_to_queue(paths, report.proposals)
    if added:
        console.print(
            f"  [cyan]synced {added} research-worthy proposal"
            f"{'s' if added != 1 else ''} to the persisted loop queue "
            f"\u2014 use[/cyan] "
            f"[bold]loop --resume --vault {paths.root}[/bold] "
            f"[cyan]to pick them up.[/cyan]"
        )

    # Show the top 3 (by priority) as a preview
    top = sorted(report.proposals, key=lambda p: (p.priority, p.title))[:3]
    console.print("\n[bold]Top proposals:[/bold]")
    for p in top:
        console.print(
            f"  [magenta]p{p.priority}[/magenta] [yellow]{p.kind.value}[/yellow] "
            f"[bold]{p.title}[/bold]"
        )
        if p.rationale:
            console.print(f"    [dim]{p.rationale}[/dim]")
    console.print(f"\n[dim]Cost: ${cost:.4f}[/dim]")


def cmd_enrich(args: argparse.Namespace) -> None:
    """Extract the universal enrichment layers into _meta/ sidecars.

    Phase A (always): mechanical, $0 — graph/salience, temporal, terms, quote-claims.
    Phase B (--mode hybrid): a bounded LLM pass types the edges + lifts propositions on
    the top-N nodes by centrality (content-hash-gated, budget-capped)."""
    from compendium.agents.enrichment import (
        enrich_vault, save_enrichment, type_edges_llm, extract_axes_llm,
    )
    from compendium.vault import VaultPaths, append_entry

    paths = VaultPaths.for_vault(args.vault)
    if not paths.is_initialized():
        console.print(
            f"[red]Vault at {paths.root} is not initialized[/red] (no CLAUDE.md)."
        )
        sys.exit(1)

    ground = not args.no_claims
    console.print(
        f"[cyan]Enriching[/cyan] {paths.root} "
        f"[dim](Phase A — mechanical, no LLM cost)[/dim]"
    )
    result = enrich_vault(paths, ground=ground)

    phaseb = None
    if args.mode == "hybrid":
        from compendium.config import CompendiumConfig
        from compendium.guardrails.cost_tracker import CostTracker
        config = CompendiumConfig()
        if not config.has_auth:
            console.print(
                "[yellow]No Anthropic auth configured — skipping Phase B "
                "(edge-typing); wrote the mechanical core only.[/yellow]"
            )
        else:
            client = config.create_anthropic_client()
            gr = config.get_guardrails()
            try:
                gr.max_cost_dollars = float(args.max_cost)
            except Exception:
                pass
            ct = CostTracker(gr)
            top_n = args.top_n if args.top_n and args.top_n > 0 else None
            do_axes = getattr(args, "axes", True)
            extractor = extract_axes_llm if do_axes else type_edges_llm
            console.print(
                f"[cyan]Phase B[/cyan] — "
                f"{'typing edges + lifting semantic axes' if do_axes else 'typing edges'} on "
                f"{'top ' + str(top_n) if top_n else f'top {int(args.top * 100)}%'} "
                f"by centrality [dim](model {args.model}, ≤${args.max_cost})[/dim]"
            )
            phaseb = extractor(
                paths, result, client=client, model=args.model, cost_tracker=ct,
                top_frac=args.top, top_n=top_n,
                progress=lambda pid, n: console.print(f"  [dim]· {pid} ({n} links)[/dim]"),
            )
            try:
                phaseb["cost"] = ct.get_summary().get("estimated_cost_usd", 0.0)
            except Exception:
                phaseb["cost"] = 0.0

    save_enrichment(paths, result)

    console.print(
        f"\n[bold]{result.page_count} nodes[/bold] · "
        f"[green]{result.edge_count}[/green] edges "
        f"({result.typed_edge_count} typed) · "
        f"[green]{len(result.temporal)}[/green] temporal · "
        f"[green]{len(result.terms)}[/green] terms · "
        f"[green]{result.claim_count}[/green] claims"
        f"{'' if result.claims_grounded else ' [dim](grounding skipped)[/dim]'}"
    )
    if phaseb is not None:
        console.print(
            f"  [bold]Phase B[/bold]: {phaseb['llm_calls']} LLM calls "
            f"({phaseb['reused']} reused unchanged) · "
            f"{phaseb['typed_edges']} edges typed · {phaseb['props']} propositions"
            + (f" · {phaseb.get('axis_records', 0)} axis records "
               f"({phaseb.get('axes_pages', 0)} pages)" if phaseb.get('axis_records') else "")
            + f" · [green]${phaseb.get('cost', 0.0):.3f}[/green]"
            + (f"  [yellow]⚠ {phaseb['failed']} parse-skipped[/yellow]"
               if phaseb.get('failed') else "")
            + ("  [yellow]⚠ stopped at budget[/yellow]" if phaseb['stopped_budget'] else "")
        )
    layers = "graph,temporal,claims,terms" + (",axes" if result.axes else "")
    console.print(
        f"  files: [cyan]wiki/_meta/enrichment-{{{layers}}}.json[/cyan] "
        "+ enrichment-report.md"
    )

    try:
        append_entry(
            paths, op="enrich", subject=f"universal enrichment ({result.method})",
            body=(
                f"- nodes: {result.page_count}\n"
                f"- edges: {result.edge_count} ({result.typed_edge_count} typed)\n"
                f"- temporal: {len(result.temporal)}\n"
                f"- terms: {len(result.terms)}\n"
                f"- claims: {result.claim_count}"
                + (f"\n- axes: {result.axis_count} records / {len(result.axes)} pages"
                   if result.axes else "")
                + (f"\n- phase B: {phaseb['llm_calls']} calls, "
                   f"{phaseb['typed_edges']} typed, ${phaseb.get('cost', 0.0):.3f}"
                   if phaseb else "")
            ),
        )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compendium — an LLM-maintained Obsidian-compatible wiki"
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    p_init = sub.add_parser("init", help="Scaffold a new vault")
    p_init.add_argument("vault", type=str, help="Vault directory path (created if missing)")
    p_init.add_argument("--topic", type=str, required=True, help="Short topic description")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="Ingest a source into a vault")
    p_ingest.add_argument(
        "source", type=str,
        help="Path to source file (.md, .txt, .pdf) OR an http(s) URL",
    )
    p_ingest.add_argument("--vault", type=str, required=True, help="Vault directory")
    p_ingest.add_argument("--max-cost", type=float, default=None, help="Budget cap in USD")
    p_ingest.add_argument("--model-strategic", type=str, default=None,
                          help="Override strategic-tier model (router)")
    p_ingest.add_argument("--model-synthesis", type=str, default=None,
                          help="Override synthesis-tier model (page writer)")
    p_ingest.add_argument("--model-mechanical", type=str, default=None,
                          help="Override mechanical-tier model (reviewer)")
    p_ingest.add_argument(
        "--no-eager-vision", action="store_true",
        help="Skip the Claude Vision pass over PDF figures. Cheaper, but "
        "code/structure embedded as bitmaps inside figures will be invisible "
        "to Router/PageWriter.",
    )
    p_ingest.add_argument(
        "--force-ocr", action="store_true",
        help="Render EVERY PDF page at --ocr-dpi and run it through Claude "
        "Vision with a dedicated OCR prompt. Discards PyMuPDF's text "
        "extraction entirely. Use for scanned books, image-only PDFs, or "
        "files with broken font cmaps where native text comes out as "
        "gibberish. Cost scales linearly with page count.",
    )
    p_ingest.add_argument(
        "--ocr-dpi", type=int, default=180,
        help="Render DPI when --force-ocr is set. 120 is enough for "
        "diagrams; 180 (default) is the floor for reliable body-text OCR; "
        "220+ for dense small type. Higher DPI = larger images = more "
        "input tokens per page.",
    )
    p_ingest.add_argument(
        "--force-reingest", action="store_true",
        help="Override the ingest-registry dedup check. Use when you want "
        "to re-run the pipeline against a source that's already been ingested "
        "(e.g. after improving prompts).",
    )
    p_ingest.add_argument(
        "--allow-skip", action="store_true",
        help=argparse.SUPPRESS,  # internal: suppress non-zero exit on dedup skip
    )
    p_ingest.add_argument(
        "--json-progress", action="store_true",
        help=argparse.SUPPRESS,  # internal: emit @@PROG@@<json> phase events for a UI
    )
    p_ingest.add_argument(
        "--no-explore", action="store_true",
        help="Skip the post-ingest Explore pass (expansion proposals). Cheaper.",
    )
    p_ingest.add_argument(
        "--extract-only", action="store_true",
        help="Stop after writing the raw .md to raw/papers/ — skip the "
        "Router/PageWriter pass entirely. Useful for batch-extracting "
        "source markdown via Gemma vision without spending on Claude. "
        "Re-run without the flag (or use loop --resume) to ingest later.",
    )
    p_ingest.add_argument(
        "--structured", action="store_true",
        help="Use the single-call non-REPL ingest agents (StructuredRouter / "
        "StructuredPageWriter / StructuredExplorer) instead of the RLM-REPL "
        "ones. Much cheaper; byte-compatible output. The web Learn builds "
        "default this on.",
    )
    p_ingest.add_argument(
        "--from-prompt", action="store_true",
        help="Treat `source` as a research PROMPT rather than a file/URL: "
        "run the cheap non-REPL gather (web search → fetch → save sources), "
        "then ingest each saved source via the structured pipeline. Implies "
        "--structured. Emits @@PROG@@ phases (search/gathered) under "
        "--json-progress.",
    )
    p_ingest.set_defaults(func=cmd_ingest)


    p_research = sub.add_parser(
        "research",
        help="Research a topic on the open web and auto-ingest the findings",
    )
    p_research.add_argument("topic", type=str, help="Topic to research")
    p_research.add_argument("--vault", type=str, required=True, help="Vault directory")
    p_research.add_argument("--max-cost", type=float, default=None, help="Budget cap in USD")
    p_research.add_argument("--model-strategic", type=str, default=None,
                            help="Override strategic-tier model (researcher + router)")
    p_research.add_argument("--model-synthesis", type=str, default=None,
                            help="Override synthesis-tier model (page writer)")
    p_research.add_argument("--model-mechanical", type=str, default=None,
                            help="Override mechanical-tier model (reviewer)")
    p_research.add_argument("--no-ingest", action="store_true",
                            help="Write sources to raw/ but don't auto-ingest them")
    p_research.add_argument(
        "--allow-no-search", action="store_true",
        help="Proceed even if no search provider is configured (research will be weak)",
    )
    p_research.set_defaults(func=cmd_research)


    p_loop = sub.add_parser(
        "loop",
        help="Autonomous research loop: research -> ingest -> lint -> explore -> select -> repeat",
    )
    p_loop.add_argument("topic", type=str, nargs="?", default=None,
                        help="Seed topic (optional when using --resume)")
    p_loop.add_argument("--vault", type=str, required=True, help="Vault directory")
    p_loop.add_argument("--resume", action="store_true",
                        help="Resume from the persisted queue at "
                        "<vault>/.loop-state.json instead of seeding from a topic. "
                        "Lets the loop compound across sessions (cron-friendly).")
    p_loop.add_argument("--max-iterations", type=int, default=3,
                        help="Max iterations this session. 0 = unlimited "
                        "(runs until queue empty or budget hit). Default 3.")
    p_loop.add_argument("--max-cost", type=float, default=None,
                        help="Total budget cap in USD across the whole loop")
    p_loop.add_argument("--auto", type=int, default=3,
                        help="Auto-select top-N proposals per iteration (default 3)")
    p_loop.add_argument("--interactive", action="store_true",
                        help="Prompt for manual selection each iteration")
    p_loop.add_argument("--no-lint", action="store_true",
                        help="Skip the lint pass each iteration (cheaper but loses drift detection)")
    p_loop.add_argument("--model-strategic", type=str, default=None)
    p_loop.add_argument("--model-synthesis", type=str, default=None)
    p_loop.add_argument("--model-mechanical", type=str, default=None)
    p_loop.set_defaults(func=cmd_loop)

    p_split = sub.add_parser(
        "split-book",
        help="Split a long PDF into chapter-sized markdown chunks in "
        "raw/articles/ (preprocessing; no LLM Router/PageWriter calls)",
    )
    p_split.add_argument("--pdf", type=str, required=True, help="Path to the PDF")
    p_split.add_argument("--vault", type=str, required=True, help="Vault directory")
    p_split.add_argument(
        "--chapters-from-toc", action="store_true",
        help="Force TOC-driven split. Default (auto) tries TOC first, "
        "falls back to fixed windows if the PDF has no usable outline.",
    )
    p_split.add_argument(
        "--pages-per-chunk", type=int, default=None,
        help="Fixed-window chunk size (pages). Set to force fixed split; "
        "default is auto-decide (TOC preferred, 25-page windows as fallback).",
    )
    p_split.add_argument(
        "--toc-depth", type=int, default=1,
        help="Max TOC level to use for boundaries (1 = top-level chapters "
        "only; 2 adds sections). Default 1.",
    )
    p_split.add_argument(
        "--min-pages-per-chunk", type=int, default=2,
        help="Merge TOC entries shorter than this (absorbs title-page-only "
        "chapter openers). Default 2.",
    )
    p_split.add_argument(
        "--title", type=str, default=None,
        help="Override the book title (otherwise read from PDF metadata, "
        "else falls back to filename).",
    )
    p_split.add_argument(
        "--ocr", action="store_true",
        help="Render every page and run it through the configured vision "
        "provider for OCR. Use for scanned books. Cost depends on provider: "
        "Claude Vision ~$0.01/page (batched), Gemma via Ollama = $0.",
    )
    p_split.add_argument(
        "--ocr-dpi", type=int, default=180,
        help="Render DPI for OCR mode. 120 for diagrams, 180 (default) for "
        "body text, 220+ for dense small type.",
    )
    p_split.add_argument(
        "--workers", type=int, default=4,
        help="Parallel OCR workers. Local Gemma saturates at 2-4 on an 8 GB "
        "GPU; Claude Vision handles 8+ fine.",
    )
    p_split.add_argument(
        "--save-renders", action="store_true",
        help="Also save rendered page PNGs into raw/assets/<book-slug>-ocr/. "
        "Useful for debugging OCR quality; costs disk space.",
    )
    p_split.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing chunk files (default skips ones that exist).",
    )
    p_split.add_argument(
        "--dry-run", action="store_true",
        help="Print the split plan without writing any files.",
    )
    p_split.set_defaults(func=cmd_split_book)

    p_explore = sub.add_parser(
        "explore", help="Generate expansion proposals for the vault"
    )
    p_explore.add_argument("--vault", type=str, required=True, help="Vault directory")
    p_explore.add_argument("--max-cost", type=float, default=None, help="Budget cap in USD")
    p_explore.add_argument("--model-strategic", type=str, default=None,
                           help="Override strategic-tier model used by Explorer")
    p_explore.set_defaults(func=cmd_explore)


    p_enrich = sub.add_parser(
        "enrich",
        help="Extract universal enrichment layers (graph/salience, temporal, terms, "
        "claims) into _meta/ sidecars (Phase A: mechanical, no LLM cost)",
    )
    p_enrich.add_argument("--vault", type=str, required=True, help="Vault directory")
    p_enrich.add_argument(
        "--mode", choices=["mechanical", "hybrid"], default="mechanical",
        help="mechanical = free Phase A only; hybrid = + bounded LLM edge-typing (Phase B)",
    )
    p_enrich.add_argument(
        "--top", type=float, default=0.2,
        help="Phase B: fraction of nodes (by centrality) to type edges on (default 0.2)",
    )
    p_enrich.add_argument(
        "--top-n", type=int, default=0,
        help="Phase B: type the top-N nodes by centrality (overrides --top)",
    )
    p_enrich.add_argument(
        "--model", type=str, default="claude-haiku-4-5",
        help="Phase B model (default claude-haiku-4-5; use a gemma* tag for $0 local)",
    )
    p_enrich.add_argument(
        "--max-cost", type=float, default=2.0,
        help="Phase B hard budget cap in USD (default 2.0)",
    )
    p_enrich.add_argument(
        "--no-claims", action="store_true",
        help="Skip the grounding-based claims layer (faster on very large vaults)",
    )
    p_enrich.add_argument(
        "--no-axes", dest="axes", action="store_false",
        help="Phase B: skip semantic-axis extraction (type edges only)",
    )
    p_enrich.set_defaults(func=cmd_enrich)


    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
