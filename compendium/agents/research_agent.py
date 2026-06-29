"""ResearchAgent — REPL-driven field researcher (typed source output).

Given a topic string, the Research agent uses `web_search`, `fetch_url`,
`deep_search`, and `llm_query` to gather material, decide what KIND of
source each finding is, and emit typed source entries. The Python
dispatcher then saves each type into the appropriate `raw/` bucket:

- `article`    → `raw/articles/<slug>.md`   (synthesized narrative)
- `paper`      → `raw/papers/<slug>.pdf`    (binary archive)
                  + `raw/papers/<slug>.md`  (summary + extracted text)
- `transcript` → `raw/transcripts/<slug>.md` (summary)
                  + `raw/transcripts/<slug>-transcript.md` (verbatim)

After each markdown file is saved, `asset_capture` scans it for
embedded image references and archives those to `raw/assets/<slug>/`.

Downstream (IngestRouter / PageWriter / Reviewer / Linter) doesn't
care which bucket a source lives in — it just reads the file path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from compendium.agents.base import BaseAgent
from compendium.models import ModelTier
from compendium.sources.asset_capture import download_assets
from compendium.sources.pdf_fetcher import (
    download_pdf,
    extract_pdf_text,
    normalize_paper_url,
)
from compendium.sources.pdf_image_extractor import (
    extract_pdf_with_figures,
    render_pages_as_markdown,
    summarize_extraction,
)
from compendium.sources.transcript_fetcher import (
    extract_video_id,
    fetch_youtube_transcript,
)
from compendium.vault import VaultPaths
from compendium.vault.pages import slugify, today_iso


RESEARCH_SYSTEM_PROMPT = """\
You are the Research Agent for a vault on **{topic_context}**.

## Your task

A user has asked you to research "**{research_topic}**". Go find
authoritative material on the open web, decide what TYPE of source
each finding is, and return a typed list of sources so they can be
archived appropriately and then ingested into the wiki.

You are NOT writing wiki pages — those are produced downstream.

## Your environment

A persistent Python REPL with these variables set:
- `research_topic` (str) — the topic you're researching
- `vault_topic` (str) — the vault's overall topic (context)
- `proposal` (dict, may be absent) — when this research pass was
  triggered by an Explorer proposal, `proposal` carries the full
  briefing: `title`, `kind` (gap|open_question|source_suggestion|...),
  `priority` (1 highest, 5 lowest), `signal` (the mechanical trigger),
  `rationale` (why this matters — often names specific existing
  pages), and `related_pages` (list of page IDs this research should
  connect to). **When `proposal` is set, read `proposal["rationale"]`
  carefully and call `read_page()` on each of `proposal["related_pages"]`
  BEFORE doing any web search** — the existing pages already contain
  knowledge you should complement, not duplicate.
- `expansion_doc` (str, may be absent) — the vault's current
  `_meta/expansion.md`. Skim this to see SIBLING proposals (other
  gaps the vault wants filled). If your research produces material
  that naturally also fills an adjacent gap, emit an extra `sources`
  entry to cover it in this same pass rather than waiting for a
  future iteration.

Functions:
- `web_search(query, num_results=5)` -> list[{{title, snippet, url}}]
- `fetch_url(url, char_limit=None)` -> page content as clean markdown
  (uses Jina Reader; handles JS-rendered pages; returns metadata +
  markdown including [[title]] and links where present)
- `deep_search(query, num_results=3)` -> search + auto-fetch top
  results, returns list[{{title, url, snippet, body_excerpt, headings}}]
- `llm_query(prompt)` -> fast sub-LLM for condensing, reformulating.
- `view_image(path)` -> dense description of a local image file.
  Rarely needed at research time (you're producing sources, not
  reading them), but useful if an already-downloaded figure in the
  vault clarifies what to research next.
- `FINAL_VAR('research_output')` to finish.

## Source types — pick the right one for each finding

Three types. Most research runs produce a mix.

**`article`** — the default. A narrative markdown synthesis of one
or more web pages on a focused subtopic. Use when the primary
sources are HTML web pages (blog posts, magazine features, wiki
articles, reference encyclopedia entries). Your `content` field IS
the source document.

**`paper`** — for academic PDFs, journal articles, or institutional
reports delivered as .pdf files. Triggers when a URL:
- ends in `.pdf` (or `.pdf?...`)
- hosts on arxiv.org, jstor.org, ssrn.com, openreview.net,
  biorxiv.org, pmc.ncbi.nlm.nih.gov, dl.acm.org, semanticscholar.org
The Python side will DOWNLOAD the binary PDF to raw/papers/<slug>.pdf
AND extract its full text into raw/papers/<slug>.md.
You only need to provide a brief `summary` (300-800 words) describing
what the paper argues and why it matters for the research topic.

**`transcript`** — for YouTube videos, interviews, talks, podcasts
with captions available. Triggers when a URL matches:
- youtube.com/watch?v=..., youtu.be/...
The Python side will pull the full timestamped transcript via the
YouTube Transcript API and save it to
raw/transcripts/<slug>-transcript.md. You provide a `summary`
(300-800 words) explaining what the video covers, who's speaking,
and which sections matter for the research topic.

## Method

1. **Orient.** Read `research_topic` and `vault_topic`. If
   `proposal` is set, print it and read `proposal["rationale"]`
   carefully — this tells you the SPECIFIC gap to fill, not just a
   topic to cover broadly. Call `read_page()` on each page in
   `proposal["related_pages"]` to see what the vault already says
   about the neighborhood; your research should COMPLEMENT those
   pages, not rewrite them. If `expansion_doc` is set, skim it for
   sibling proposals — opportunities to fill multiple adjacent gaps
   in one research pass.

2. **Survey.** Run 2-4 `web_search` / `deep_search` calls. Prefer
   authoritative sources: primary texts, academic papers, landmark
   reviews, canonical reference articles. Skim, don't exhaustively read.

3. **Classify each finding by URL:**
   - .pdf or paper-host → emit as `paper`
   - youtube.com / youtu.be → emit as `transcript`
   - anything else → use as fodder for an `article` synthesis

4. **For articles:** `fetch_url` 2-4 relevant pages, synthesize into
   a focused ~800-1500-word markdown document. Required structure:

   ```
   # <Title>
   <2-3 sentence abstract.>
   ## <Subheading>
   <body with attribution>
   ...
   ## Sources
   - <bibliographic entry 1>
   - <URL 1 for web-only>
   ```

   The `## Sources` section is load-bearing — the Linter's citation
   verifier parses it. Include author/title/publisher/year when
   knowable; URLs acceptable for purely web sources.

   **Preserve code verbatim.** If a fetched page contains code,
   pseudocode, API signatures, or command snippets that illustrate
   the concept, transcribe them into fenced code blocks
   (```python / ```bash / ```) in your article — don't summarize.
   The downstream PageWriter depends on concrete code being present;
   paraphrased code loses the tokens that later queries need to
   retrieve.

5. **For papers / transcripts:** do NOT fetch the full content
   yourself (the Python side does the binary download / transcript
   fetch). Just write a focused 300-800 word SUMMARY and include the
   source URL. The verbatim content is archived separately.

6. **Scope discipline.** Don't emit a "Topic: An Introduction"
   article — that's a shallow smell. Either the topic is narrow (one
   focused source) or broad (2-5 focused subtopic docs). Never more
   than 5 sources per research run total.

## Output

Build a dict named `research_output` and call `FINAL_VAR('research_output')`:

```python
research_output = {{
    "sources": [
        # Article (default — narrative synthesis you wrote):
        {{
            "type": "article",
            "title": str,
            "slug": str,             # kebab-case; becomes <slug>.md
            "content": str,          # full markdown body incl. # heading
                                     # + ## Sources bibliography
        }},
        # Paper (Python downloads the PDF — you provide pdf_url + summary):
        {{
            "type": "paper",
            "title": str,
            "slug": str,
            "pdf_url": str,          # direct .pdf URL (arxiv abs URLs
                                     # are auto-converted to pdf URLs)
            "summary": str,          # 300-800 word summary of the paper
        }},
        # Transcript (Python fetches the transcript — you provide
        # video_url + summary):
        {{
            "type": "transcript",
            "title": str,
            "slug": str,
            "video_url": str,        # YouTube URL
            "summary": str,          # 300-800 word summary of the video
        }},
    ],
    "notes": str,                    # 1-2 sentences on what you covered
                                     # vs. deliberately left out
}}
```

Aim for 1-5 sources total across all types. Slugs must be unique.

## Opening moves

1. `print(research_topic, vault_topic)`
2. `results = web_search(research_topic, num_results=6); print(results)`
3. Classify returned URLs by type and decide your mix.
4. For each article source: fetch + synthesize. For paper/transcript
   sources: just write the summary.
5. Assemble `research_output`, call `FINAL_VAR('research_output')`.
"""


# ----- result types ---------------------------------------------------------


@dataclass
class ResearchSource:
    type: str          # "article" | "paper" | "transcript"
    title: str
    slug: str
    content: str       # markdown that will be saved and then ingested
    extra_paths: list[Path] = field(default_factory=list)   # pdf / transcript verbatim / assets
    errors: list[str] = field(default_factory=list)         # soft failures (e.g. PDF 404)


@dataclass
class ResearchResult:
    topic: str
    sources: list[ResearchSource]
    notes: str
    raw_paths: list[Path]   # the .md files that should be ingested next


# ----- agent ----------------------------------------------------------------


class ResearchAgent(BaseAgent):
    """REPL field-researcher producing typed source files in raw/."""

    def __init__(self, *args, vault: VaultPaths, **kwargs):
        tiered = kwargs.pop("tiered", None)
        if tiered is not None:
            kwargs.setdefault("model_override", tiered.get_model(ModelTier.STRATEGIC))
            kwargs.setdefault(
                "sub_call_model_override",
                tiered.get_model(ModelTier.SYNTHESIS),
            )
        super().__init__(*args, **kwargs)
        self._vault = vault

    _current_topic: str = ""

    def get_system_prompt(self) -> str:
        topic_context = _read_topic(self._vault) or "(unspecified)"
        research_topic = self._current_topic or "(unset)"
        return RESEARCH_SYSTEM_PROMPT.format(
            topic_context=topic_context, research_topic=research_topic
        )

    def research(
        self,
        topic: str,
        *,
        proposal=None,
        expansion_doc_text: str | None = None,
    ) -> ResearchResult:
        """Run the research REPL, write all source artifacts to raw/.

        Produces the markdown files the ingest pipeline will consume,
        plus any side artifacts (PDFs, transcript verbatim, downloaded
        assets) into the appropriate raw/ subdirs.

        When `proposal` is an ExpansionProposal, its signal, rationale,
        related pages, and priority are injected into the REPL context
        so research is surgical rather than generic. When
        `expansion_doc_text` is provided (the current
        `wiki/_meta/expansion.md`), the agent sees adjacent proposals
        too — sometimes its research naturally covers multiple gaps at
        once and should emit extra sources to capture them.
        """
        self._current_topic = topic
        context: dict[str, Any] = {
            "research_topic": topic,
            "vault_topic": _read_topic(self._vault),
        }
        if proposal is not None:
            context["proposal"] = {
                "title": proposal.title,
                "kind": proposal.kind.value,
                "priority": proposal.priority,
                "signal": proposal.signal,
                "rationale": proposal.rationale,
                "related_pages": list(proposal.related),
            }
        if expansion_doc_text:
            # Keep context reasonable — expansion.md is already concise
            # but cap to ~25K chars just in case.
            context["expansion_doc"] = expansion_doc_text[:25_000]
        raw = self.run(context)
        if not isinstance(raw, dict):
            raise ValueError(
                f"ResearchAgent returned {type(raw).__name__}, expected dict"
            )

        raw_sources = raw.get("sources") or []
        if not isinstance(raw_sources, list):
            raise ValueError("ResearchAgent output: 'sources' must be a list")

        # Ensure the raw subdirs exist
        self._vault.raw_articles.mkdir(parents=True, exist_ok=True)
        self._vault.raw_papers.mkdir(parents=True, exist_ok=True)
        self._vault.raw_transcripts.mkdir(parents=True, exist_ok=True)
        self._vault.raw_assets.mkdir(parents=True, exist_ok=True)

        produced: list[ResearchSource] = []
        md_paths: list[Path] = []
        used_slugs: set[str] = set()
        for entry in raw_sources:
            if not isinstance(entry, dict):
                continue
            try:
                source = self._dispatch_source(
                    entry, topic=topic, used_slugs=used_slugs
                )
            except Exception as exc:
                produced.append(
                    ResearchSource(
                        type=str(entry.get("type") or "unknown"),
                        title=str(entry.get("title") or "(unknown)"),
                        slug="(error)",
                        content="",
                        errors=[f"dispatch failed: {exc}"],
                    )
                )
                continue
            if source is None:
                continue
            produced.append(source)
            used_slugs.add(source.slug)
            # The primary .md of each source is what gets ingested next.
            md_paths.append(Path(source.extra_paths[0]) if source.extra_paths[:1] and source.extra_paths[0].suffix == ".md" else _primary_md_path(self._vault, source))

        # Filter to ones with a real .md path
        md_paths = [p for p in md_paths if p is not None and p.exists()]

        return ResearchResult(
            topic=topic,
            sources=produced,
            notes=(raw.get("notes") or "").strip(),
            raw_paths=md_paths,
        )

    # ---- per-source dispatcher --------------------------------------------

    def _dispatch_source(
        self, entry: dict, *, topic: str, used_slugs: set[str]
    ) -> ResearchSource | None:
        stype = (entry.get("type") or "article").strip().lower()
        title = (entry.get("title") or "").strip()
        slug_seed = entry.get("slug") or title
        if not title or not slug_seed:
            return None

        if stype == "article":
            return self._save_article(entry, title, slug_seed, used_slugs, topic)
        if stype == "paper":
            return self._save_paper(entry, title, slug_seed, used_slugs, topic)
        if stype == "transcript":
            return self._save_transcript(entry, title, slug_seed, used_slugs, topic)
        # Unknown type — treat as article, best effort
        return self._save_article(entry, title, slug_seed, used_slugs, topic)

    # ---- article ---------------------------------------------------------

    def _save_article(
        self,
        entry: dict,
        title: str,
        slug_seed: str,
        used_slugs: set[str],
        topic: str,
    ) -> ResearchSource | None:
        content = (entry.get("content") or "").strip()
        if not content:
            return None
        slug = _unique_slug_in_dir(
            slugify(slug_seed), used_slugs, self._vault.raw_articles
        )
        if not content.startswith("# "):
            content = f"# {title}\n\n{content}".rstrip()
        target = self._vault.raw_articles / f"{slug}.md"
        header = _provenance_header(topic=topic, source_type="article")
        target.write_text(header + content + "\n", encoding="utf-8", newline="\n")

        assets = _capture_assets(self._vault, slug, content)
        return ResearchSource(
            type="article",
            title=title,
            slug=slug,
            content=content,
            extra_paths=[target, *assets],
        )

    # ---- paper -----------------------------------------------------------

    def _save_paper(
        self,
        entry: dict,
        title: str,
        slug_seed: str,
        used_slugs: set[str],
        topic: str,
    ) -> ResearchSource | None:
        pdf_url = (entry.get("pdf_url") or "").strip()
        summary = (entry.get("summary") or entry.get("content") or "").strip()
        if not pdf_url or not summary:
            return None
        pdf_url = normalize_paper_url(pdf_url)
        slug = _unique_slug_in_dir(
            slugify(slug_seed), used_slugs, self._vault.raw_papers
        )

        # Respect the vault's ingest registry: skip the download entirely
        # if this URL has already been ingested on a prior run.
        from compendium.vault import IngestRegistry, RegistryEntry, hash_file, now_iso

        registry = IngestRegistry(self._vault)
        # Tombstone check FIRST — a culled source should not be
        # re-downloaded even if the URL hash changes slightly. The
        # Mender's Tier-4 decision is load-bearing: we respect it.
        tomb = registry.is_tombstoned(url=pdf_url)
        if tomb is not None:
            return ResearchSource(
                type="paper",
                title=title,
                slug=tomb.get("source_id", "tombstoned"),
                content="(tombstoned — previously culled by Mender tier 4)",
                extra_paths=[],
                errors=[
                    f"tombstoned: {tomb.get('reason', 'no reason recorded')}"
                ],
            )
        hit = registry.find_by_url(pdf_url)
        if hit is not None:
            # The paper is already in the vault — return a zero-cost
            # "skipped" ResearchSource so the orchestrator logs it
            # without re-downloading or re-ingesting.
            return ResearchSource(
                type="paper",
                title=title,
                slug=hit.source_id,
                content=f"(already ingested on {hit.ingested})",
                extra_paths=[],
                errors=[f"dedup: paper already ingested as {hit.source_id}"],
            )

        errors: list[str] = []
        pdf_path: Path | None = self._vault.raw_papers / f"{slug}.pdf"
        if not download_pdf(pdf_url, pdf_path):
            errors.append(f"PDF download failed: {pdf_url}")
            pdf_path = None

        # Post-download content-hash dedup. Catches the common case
        # where a paper was previously ingested from a local file (no
        # URL recorded) and now the research agent found the same paper
        # at an arxiv / openreview URL. URL lookup would miss; byte
        # comparison catches it deterministically.
        if pdf_path is not None:
            downloaded_hash = hash_file(pdf_path)
            # Tombstone by hash — catches a culled paper that got
            # re-found at a different URL. Delete the download and
            # return an informative ResearchSource.
            tomb_by_hash = registry.is_tombstoned(hash=downloaded_hash)
            if tomb_by_hash is not None:
                try:
                    pdf_path.unlink()
                except OSError:
                    pass
                return ResearchSource(
                    type="paper",
                    title=title,
                    slug=tomb_by_hash.get("source_id", "tombstoned"),
                    content="(tombstoned by hash — previously culled)",
                    extra_paths=[],
                    errors=[
                        f"tombstoned: {tomb_by_hash.get('reason', '?')}"
                    ],
                )
            hash_hit = registry.find_by_hash(downloaded_hash)
            if hash_hit is not None:
                # Delete the downloaded binary — it's a bit-for-bit copy
                # of what we already have in the vault.
                try:
                    pdf_path.unlink()
                except OSError:
                    pass
                # Record this URL against the existing source_id so a
                # future run by URL alone lands on the known entry.
                try:
                    registry.record(
                        RegistryEntry(
                            source_id=hash_hit.source_id,
                            raw_path=hash_hit.raw_path,
                            ingested=hash_hit.ingested,
                            hash=hash_hit.hash,
                            url=pdf_url,
                            origin=hash_hit.origin or pdf_url,
                        )
                    )
                except Exception:
                    pass
                return ResearchSource(
                    type="paper",
                    title=title,
                    slug=hash_hit.source_id,
                    content=f"(content-identical to already-ingested {hash_hit.source_id})",
                    extra_paths=[],
                    errors=[
                        f"dedup: PDF at {pdf_url} is byte-identical to "
                        f"already-ingested {hash_hit.source_id} "
                        f"(ingested {hash_hit.ingested})"
                    ],
                )

        # Prefer PyMuPDF (text + figures); fall back to pypdf text-only.
        # When figures exist, also run vision over them so any code
        # embedded as bitmap becomes text-visible to downstream Router/Writer.
        # Backend is picked by config.vision_provider (anthropic | ollama).
        from compendium.sources.pdf_image_extractor import describe_pdf_figures
        from compendium.sources.vision_provider import make_vision_provider

        extracted = ""
        figure_count = 0
        rendered_count = 0
        described_count = 0
        if pdf_path:
            assets_dir = self._vault.raw_assets / slug
            pages = extract_pdf_with_figures(pdf_path, assets_dir)
            if pages:
                vision_provider_name = (
                    getattr(self.config, "vision_provider", "anthropic") or "anthropic"
                ).lower()
                vision_provider = make_vision_provider(
                    config=self.config,
                    client=self.client,
                    cost_tracker=self.cost_tracker,
                    model_override=(
                        self.effective_sub_call_model
                        if vision_provider_name == "anthropic"
                        else None
                    ),
                )
                descriptions = describe_pdf_figures(
                    pages,
                    provider=vision_provider,
                )
                described_count = len(descriptions)
                extracted = render_pages_as_markdown(
                    pages,
                    assets_rel_base=f"../assets/{slug}",
                    figure_descriptions=descriptions,
                )
                stats = summarize_extraction(pages)
                figure_count = stats["embedded_figures"]
                rendered_count = stats["rendered_pages"]
            else:
                extracted = extract_pdf_text(pdf_path)

        header = _provenance_header(
            topic=topic, source_type="paper", source_url=pdf_url
        )
        body_parts = [header, f"# {title}", "", summary]
        if rendered_count or figure_count:
            note = []
            if rendered_count:
                note.append(f"{rendered_count} figure-heavy page(s) rendered")
            if figure_count:
                note.append(f"{figure_count} embedded figure(s) extracted")
            body_parts.extend(
                ["", f"_Figures: {'; '.join(note)} → `raw/assets/{slug}/`._"]
            )
        if extracted:
            body_parts.extend(["", "## Extracted content", "", extracted])
        body = "\n".join(body_parts).rstrip() + "\n"
        md_path = self._vault.raw_papers / f"{slug}.md"
        md_path.write_text(body, encoding="utf-8", newline="\n")

        extras: list[Path] = [md_path]
        if pdf_path:
            extras.append(pdf_path)
        extras.extend(_capture_assets(self._vault, slug, summary))

        # Record the successful download in the registry so future runs
        # (including this vault's subsequent loop iterations) skip it.
        try:
            registry.record(
                RegistryEntry(
                    source_id=slug,
                    raw_path=md_path.relative_to(self._vault.root).as_posix(),
                    ingested=now_iso(),
                    hash=hash_file(pdf_path) if pdf_path else "",
                    url=pdf_url,
                    origin=pdf_url,
                )
            )
        except Exception:
            pass

        return ResearchSource(
            type="paper",
            title=title,
            slug=slug,
            content=body,
            extra_paths=extras,
            errors=errors,
        )

    # ---- transcript ------------------------------------------------------

    def _save_transcript(
        self,
        entry: dict,
        title: str,
        slug_seed: str,
        used_slugs: set[str],
        topic: str,
    ) -> ResearchSource | None:
        video_url = (entry.get("video_url") or "").strip()
        summary = (entry.get("summary") or entry.get("content") or "").strip()
        if not video_url or not summary:
            return None
        slug = _unique_slug_in_dir(
            slugify(slug_seed), used_slugs, self._vault.raw_transcripts
        )

        errors: list[str] = []
        transcript_text = fetch_youtube_transcript(video_url)
        transcript_path: Path | None = None
        if transcript_text:
            transcript_path = (
                self._vault.raw_transcripts / f"{slug}-transcript.md"
            )
            video_id = extract_video_id(video_url) or ""
            transcript_path.write_text(
                f"<!-- source: {video_url} -->\n"
                f"<!-- video_id: {video_id} -->\n"
                f"<!-- fetched: {today_iso()} -->\n\n"
                f"# {title} — transcript\n\n{transcript_text}\n",
                encoding="utf-8",
                newline="\n",
            )
        else:
            errors.append(f"transcript unavailable for {video_url}")

        header = _provenance_header(
            topic=topic, source_type="transcript", source_url=video_url
        )
        body_parts = [header, f"# {title}", "", summary]
        if transcript_path:
            rel = transcript_path.name
            body_parts.extend(
                [
                    "",
                    "## Transcript",
                    "",
                    f"Verbatim transcript saved at `{rel}` — see "
                    f"[[{slug}-transcript]] for line-by-line captions.",
                ]
            )
        body = "\n".join(body_parts).rstrip() + "\n"
        md_path = self._vault.raw_transcripts / f"{slug}.md"
        md_path.write_text(body, encoding="utf-8", newline="\n")

        extras: list[Path] = [md_path]
        if transcript_path:
            extras.append(transcript_path)
        extras.extend(_capture_assets(self._vault, slug, summary))
        return ResearchSource(
            type="transcript",
            title=title,
            slug=slug,
            content=body,
            extra_paths=extras,
            errors=errors,
        )


# ----- helpers --------------------------------------------------------------


def _read_topic(vault: VaultPaths) -> str:
    if not vault.claude_md.exists():
        return ""
    text = vault.claude_md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# Vault Schema"):
            _, _, after = line.partition("—")
            return after.strip()
    return ""


def _unique_slug_in_dir(base: str, used: set[str], dir_: Path) -> str:
    candidate = base or "research-source"
    n = 2
    while candidate in used or (dir_ / f"{candidate}.md").exists() or (
        dir_ / f"{candidate}.pdf"
    ).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _provenance_header(
    *, topic: str, source_type: str, source_url: str | None = None
) -> str:
    lines = [
        f"<!-- research_topic: {topic} -->",
        f"<!-- source_type: {source_type} -->",
    ]
    if source_url:
        lines.append(f"<!-- source_url: {source_url} -->")
    lines.append(f"<!-- researched: {today_iso()} -->")
    return "\n".join(lines) + "\n\n"


def _capture_assets(
    vault: VaultPaths, slug: str, markdown: str
) -> list[Path]:
    """Best-effort image download for a source's referenced assets."""
    assets_subdir = vault.raw_assets / slug
    try:
        return download_assets(markdown, assets_subdir, max_per_source=8)
    except Exception:
        return []


def _primary_md_path(vault: VaultPaths, source: ResearchSource) -> Path | None:
    """The .md file that should be ingested next. `paper` and `transcript`
    types save their primary .md in their own bucket, not raw/articles/.
    """
    bucket = {
        "article": vault.raw_articles,
        "paper": vault.raw_papers,
        "transcript": vault.raw_transcripts,
    }.get(source.type, vault.raw_articles)
    candidate = bucket / f"{source.slug}.md"
    return candidate if candidate.exists() else None
