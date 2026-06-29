"""index.md maintenance — the vault's content-oriented catalog.

The index is regenerated from the current state of the vault on every
ingest. Small vaults get a single flat `index.md`; once the vault
crosses `HIERARCHICAL_THRESHOLD` pages, `index.md` becomes a concise
Table of Contents and full per-category listings are emitted as
separate `index-<type>.md` files at the vault root. Agents read
`index.md` first; they follow the TOC pointers into the category
files only when they need the full listing.

Small-vault layout:

    # Index
    *topic · updated YYYY-MM-DD · N pages*

    ## Concepts
    - [[page]] — summary
    ## Entities
    ...

Large-vault layout:

    # Index
    *topic · updated YYYY-MM-DD · 523 pages*

    ## Concepts (287) — full list in [[index-concepts]]
    Most recent:
    - [[…]] — summary
    - …

    ## Entities (145) — full list in [[index-entities]]
    …

    (…and separate `index-concepts.md`, `index-entities.md`, … at root)
"""

from __future__ import annotations

from compendium.models import PageType
from compendium.vault.layout import VaultPaths
from compendium.vault.pages import list_pages, read_page, today_iso


_CATEGORY_ORDER: list[tuple[PageType, str]] = [
    (PageType.CONCEPT, "Concepts"),
    (PageType.ENTITY, "Entities"),
    (PageType.SYNTHESIS, "Syntheses"),
    (PageType.SOURCE, "Sources"),
]


# Above this many pages, index.md becomes a TOC and category files are
# emitted alongside. Below it, the flat single-file layout is kept.
HIERARCHICAL_THRESHOLD = 100

# For the TOC mode, how many recent entries per category to show inline.
TOC_PREVIEW_PER_CATEGORY = 10


def _collect_pages_by_type(
    paths: VaultPaths,
) -> tuple[dict[PageType, list[tuple[str, str, str, str]]], int]:
    """Return ({type -> [(id, title, summary, updated)]}, total).

    Dedupes by page_id: when the same slug exists in multiple type
    directories (an unresolved duplicate-slug collision that Mender
    tier-1 would escalate), `list_pages` yields it once per directory
    and `read_page` always returns the first-hit copy. Without dedup
    each collision rendered as two identical index entries. Mender
    escalates the collision; here we just avoid the cosmetic bug.
    """
    by_type: dict[PageType, list[tuple[str, str, str, str]]] = {
        pt: [] for pt in PageType
    }
    seen_ids: set[str] = set()
    total = 0
    for page_id in list_pages(paths):
        if page_id in seen_ids:
            continue
        page = read_page(paths, page_id)
        if page is None:
            continue
        seen_ids.add(page_id)
        total += 1
        by_type[page.type].append(
            (page.id, page.title, page.summary, page.updated)
        )
    return by_type, total


def _render_entry(title: str, summary: str) -> str:
    if summary:
        return f"- [[{title}]] — {summary}"
    return f"- [[{title}]]"


_CATEGORY_PLURAL_SLUG: dict[PageType, str] = {
    PageType.CONCEPT: "concepts",
    PageType.ENTITY: "entities",
    PageType.SOURCE: "sources",
    PageType.SYNTHESIS: "syntheses",
}


def _category_filename(pt: PageType) -> str:
    """`index-concepts.md`, `index-entities.md`, `index-sources.md`,
    `index-syntheses.md`."""
    return f"index-{_CATEGORY_PLURAL_SLUG[pt]}.md"


def _render_flat_index(
    by_type: dict[PageType, list[tuple[str, str, str, str]]],
    total: int,
    topic: str | None,
) -> str:
    """Compact flat layout — every page listed in one file."""
    lines: list[str] = ["# Index", ""]
    header_parts = [f"updated {today_iso()}", f"{total} page{'s' if total != 1 else ''}"]
    if topic:
        header_parts.insert(0, topic)
    lines.append("*" + " · ".join(header_parts) + "*")
    lines.append("")

    for pt, heading in _CATEGORY_ORDER:
        rows = by_type[pt]
        if not rows:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for _, title, summary, _updated in sorted(
            rows, key=lambda r: r[1].lower()
        ):
            lines.append(_render_entry(title, summary))
        lines.append("")

    if total == 0:
        lines.append("_Vault is empty. Ingest a source to populate it._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_toc_index(
    by_type: dict[PageType, list[tuple[str, str, str, str]]],
    total: int,
    topic: str | None,
) -> str:
    """TOC layout — counts per category + preview of most recent."""
    lines: list[str] = ["# Index", ""]
    header_parts = [f"updated {today_iso()}", f"{total} pages"]
    if topic:
        header_parts.insert(0, topic)
    lines.append("*" + " · ".join(header_parts) + "*")
    lines.append("")
    lines.append(
        "> This vault has crossed the hierarchical threshold. Per-category "
        "full listings are in separate `index-<type>s.md` files at the vault "
        "root. The preview below shows the most-recently-updated entries per "
        "category."
    )
    lines.append("")

    for pt, heading in _CATEGORY_ORDER:
        rows = by_type[pt]
        if not rows:
            continue
        link_stem = _category_filename(pt).removesuffix(".md")
        lines.append(
            f"## {heading} ({len(rows)}) — full list in [[{link_stem}]]"
        )
        lines.append("")
        # Most recent: sort by `updated` descending (falls back to title for ties)
        recent = sorted(
            rows, key=lambda r: (r[3] or "", r[1].lower()), reverse=True
        )[:TOC_PREVIEW_PER_CATEGORY]
        lines.append("Most recent:")
        lines.append("")
        for _, title, summary, _updated in recent:
            lines.append(_render_entry(title, summary))
        if len(rows) > TOC_PREVIEW_PER_CATEGORY:
            lines.append(
                f"- _…{len(rows) - TOC_PREVIEW_PER_CATEGORY} more, "
                f"see [[{link_stem}]]_"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_category_index(
    pt: PageType,
    heading: str,
    rows: list[tuple[str, str, str, str]],
    topic: str | None,
    total_pages: int,
) -> str:
    lines: list[str] = [f"# {heading} Index", ""]
    header_parts = [
        f"updated {today_iso()}",
        f"{len(rows)} {pt.value}{'s' if len(rows) != 1 else ''}",
        f"{total_pages} pages total in vault",
    ]
    if topic:
        header_parts.insert(0, topic)
    lines.append("*" + " · ".join(header_parts) + "*")
    lines.append("")
    lines.append(f"> Back to the vault TOC: [[index]].")
    lines.append("")
    for _, title, summary, _updated in sorted(rows, key=lambda r: r[1].lower()):
        lines.append(_render_entry(title, summary))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_index(paths: VaultPaths, *, topic: str | None = None) -> str:
    """Render `index.md` content for the current vault state.

    Returns the flat layout when total pages <= HIERARCHICAL_THRESHOLD;
    otherwise returns the TOC layout. For the file-writing side
    (including emitting per-category files when hierarchical),
    use `write_index` instead.
    """
    by_type, total = _collect_pages_by_type(paths)
    if total <= HIERARCHICAL_THRESHOLD:
        return _render_flat_index(by_type, total, topic)
    return _render_toc_index(by_type, total, topic)


def write_index(paths: VaultPaths, *, topic: str | None = None) -> None:
    """Regenerate index.md — and, above the threshold, per-category files."""
    by_type, total = _collect_pages_by_type(paths)
    if total <= HIERARCHICAL_THRESHOLD:
        paths.index_md.write_text(
            _render_flat_index(by_type, total, topic), encoding="utf-8"
        )
        # Clean up stale category files from when the vault was bigger.
        for pt, _heading in _CATEGORY_ORDER:
            stale = paths.root / _category_filename(pt)
            if stale.exists():
                try:
                    stale.unlink()
                except OSError:
                    pass
        return

    # Hierarchical: TOC + per-category files
    paths.index_md.write_text(
        _render_toc_index(by_type, total, topic), encoding="utf-8"
    )
    for pt, heading in _CATEGORY_ORDER:
        rows = by_type[pt]
        target = paths.root / _category_filename(pt)
        if not rows:
            # No pages of this type — remove any stale file
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            continue
        target.write_text(
            _render_category_index(pt, heading, rows, topic, total),
            encoding="utf-8",
        )
