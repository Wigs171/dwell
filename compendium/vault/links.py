"""Wikilink parsing and backlink graph construction.

Supports:
- `[[Page]]` — link by title or ID
- `[[Page|alias]]` — piped alias text
- `![[Page]]` — transclude (treated as a link for graph purposes)

Link targets are normalized to page IDs via `slugify` so `[[Hermes
Trismegistus]]` and `[[hermes-trismegistus]]` resolve to the same node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from compendium.models import PageType
from compendium.vault.layout import VaultPaths
from compendium.vault.pages import list_pages, read_page, slugify


_WIKILINK_RE = re.compile(r"!?\[\[([^\[\]\n]+?)\]\]")


@dataclass(frozen=True)
class WikiLink:
    raw: str             # exact text inside [[...]]
    target: str          # text before '|', normalized via slugify
    display: str         # text after '|' if present, else target text
    transclude: bool     # prefixed with '!'


def parse_wikilinks(text: str) -> list[WikiLink]:
    """Extract every wikilink from a markdown body."""
    out: list[WikiLink] = []
    for m in _WIKILINK_RE.finditer(text):
        inner = m.group(1).strip()
        transclude = m.group(0).startswith("!")
        if "|" in inner:
            target_raw, display = (p.strip() for p in inner.split("|", 1))
        else:
            target_raw = inner
            display = inner
        out.append(
            WikiLink(
                raw=inner,
                target=slugify(target_raw),
                display=display,
                transclude=transclude,
            )
        )
    return out


def build_alias_index(paths: VaultPaths) -> dict[str, str]:
    """Map alias-slugs to the canonical page ID they resolve to.

    Also maps each page ID to itself so lookups are uniform.
    """
    alias_map: dict[str, str] = {}
    for page_id in list_pages(paths):
        page = read_page(paths, page_id)
        if page is None:
            continue
        alias_map[page.id] = page.id
        alias_map[slugify(page.title)] = page.id
        for alias in page.aliases:
            alias_map[slugify(alias)] = page.id
    return alias_map


def resolve_target(target_slug: str, alias_map: dict[str, str]) -> str | None:
    """Resolve a wikilink target to a canonical page ID, or None if unresolved."""
    return alias_map.get(target_slug)


def build_backlinks(paths: VaultPaths) -> dict[str, list[str]]:
    """For each page, list other page IDs that wikilink to it.

    Keys are canonical page IDs. Pages with no inbound links are absent
    from the mapping (use `find_orphans` for those).
    """
    alias_map = build_alias_index(paths)
    backlinks: dict[str, list[str]] = {}
    for page_id in list_pages(paths):
        page = read_page(paths, page_id)
        if page is None:
            continue
        for link in parse_wikilinks(page.body):
            canonical = resolve_target(link.target, alias_map)
            if canonical and canonical != page.id:
                backlinks.setdefault(canonical, []).append(page.id)
    return backlinks


def find_orphans(paths: VaultPaths) -> list[str]:
    """Page IDs with zero inbound wikilinks (excluding source pages).

    Source summary pages are deliberately excluded: they exist to be
    referenced, but only from their specific consuming pages, so absence
    of backlinks isn't a useful signal for them.
    """
    backlinks = build_backlinks(paths)
    all_ids = list_pages(paths)
    orphans: list[str] = []
    for page_id in all_ids:
        if page_id in backlinks:
            continue
        page = read_page(paths, page_id)
        if page is None or page.type == PageType.SOURCE:
            continue
        orphans.append(page_id)
    return orphans


def find_broken_wikilinks(paths: VaultPaths) -> list[tuple[str, str]]:
    """Wikilinks pointing at non-existent pages.

    Returns list of (source_page_id, target_slug) tuples. Explorer treats
    these as gap signals — each broken link is a page the corpus wants.
    """
    alias_map = build_alias_index(paths)
    broken: list[tuple[str, str]] = []
    for page_id in list_pages(paths):
        page = read_page(paths, page_id)
        if page is None:
            continue
        seen: set[str] = set()
        for link in parse_wikilinks(page.body):
            if link.target in seen:
                continue
            seen.add(link.target)
            if resolve_target(link.target, alias_map) is None:
                broken.append((page_id, link.target))
    return broken


def format_wikilink(page_id_or_title: str, alias: str | None = None) -> str:
    """Render a wikilink string. Prefer titles for readability."""
    if alias:
        return f"[[{page_id_or_title}|{alias}]]"
    return f"[[{page_id_or_title}]]"
