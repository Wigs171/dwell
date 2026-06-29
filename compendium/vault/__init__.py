"""Vault I/O — pages, wikilinks, index, log.

Central package for operating on Obsidian-compatible vault directories.
See compendium.vault.layout for directory conventions.
"""

from compendium.vault.layout import VaultPaths
from compendium.vault.pages import (
    page_exists,
    page_path,
    parse_page_markdown,
    read_page,
    render_page_markdown,
    slugify,
    today_iso,
    write_page,
    list_pages,
    locate_page,
)
from compendium.vault.links import (
    WikiLink,
    build_alias_index,
    build_backlinks,
    find_broken_wikilinks,
    find_orphans,
    format_wikilink,
    parse_wikilinks,
    resolve_target,
)
from compendium.vault.index import render_index, write_index
from compendium.vault.log import append_entry, read_recent, timestamp_iso
from compendium.vault.schema import render_claude_md
from compendium.vault.registry import (
    IngestRegistry,
    RegistryEntry,
    hash_file,
    now_iso,
)
from compendium.vault.loop_state import sync_proposals_to_queue
from compendium.vault.backlog import (
    BacklogEntry,
    PageBacklog,
    render_backlog_md,
    write_backlog_md,
)
from compendium.vault.history import append_history_entry, read_history
from compendium.vault.contradiction_ledger import (
    ContradictionLedger,
    LedgerEntry,
    ReconcileResult,
    make_key,
    short_id,
    STATUS_OPEN,
    STATUS_RESOLVED,
    STATUS_BY_DESIGN,
)

__all__ = [
    "VaultPaths",
    # pages
    "slugify",
    "today_iso",
    "page_path",
    "locate_page",
    "list_pages",
    "read_page",
    "write_page",
    "page_exists",
    "parse_page_markdown",
    "render_page_markdown",
    # links
    "WikiLink",
    "parse_wikilinks",
    "build_alias_index",
    "resolve_target",
    "build_backlinks",
    "find_orphans",
    "find_broken_wikilinks",
    "format_wikilink",
    # index / log / schema
    "render_index",
    "write_index",
    "append_entry",
    "read_recent",
    "timestamp_iso",
    "render_claude_md",
    # registry + loop state
    "IngestRegistry",
    "RegistryEntry",
    "hash_file",
    "now_iso",
    "sync_proposals_to_queue",
    # page backlog
    "BacklogEntry",
    "PageBacklog",
    "render_backlog_md",
    "write_backlog_md",
    # lint history
    "append_history_entry",
    "read_history",
    # contradiction ledger
    "ContradictionLedger",
    "LedgerEntry",
    "ReconcileResult",
    "make_key",
    "short_id",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "STATUS_BY_DESIGN",
]
