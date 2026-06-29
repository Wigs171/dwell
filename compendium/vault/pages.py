"""Page CRUD with YAML frontmatter.

A page is stored on disk as a markdown file with YAML frontmatter:

    ---
    id: page-id
    title: Page Title
    type: concept
    summary: one-line summary
    tags: [tag-1]
    aliases: []
    sources: [source-id-1]
    updated: 2026-04-18
    ---

    # Page Title

    Body content with [[wikilinks]].

All I/O is UTF-8. Writes are atomic (tmp file + os.replace).
"""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import yaml

from compendium.models import Page, PageType
from compendium.vault.layout import VaultPaths


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?\n)---\s*\n?(.*)\Z",
    re.DOTALL,
)

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Convert arbitrary text into a kebab-case ASCII slug.

    Unicode is normalized and non-ASCII characters are dropped. Runs of
    non-alphanumeric characters collapse to a single hyphen. Leading and
    trailing hyphens are stripped. Empty input yields 'untitled'.
    """
    if not text:
        return "untitled"
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP_RE.sub("-", ascii_text).strip("-")
    return slug or "untitled"


def today_iso() -> str:
    """Today's date as YYYY-MM-DD in the local timezone."""
    return date.today().isoformat()


def _type_dir(paths: VaultPaths, page_type: PageType) -> Path:
    return {
        PageType.ENTITY: paths.entities,
        PageType.CONCEPT: paths.concepts,
        PageType.SOURCE: paths.sources,
        PageType.SYNTHESIS: paths.syntheses,
    }[page_type]


def page_path(paths: VaultPaths, page_id: str, page_type: PageType) -> Path:
    """Canonical path for a page given its id and type."""
    return _type_dir(paths, page_type) / f"{page_id}.md"


def locate_page(paths: VaultPaths, page_id: str) -> Path | None:
    """Find the on-disk path for a page when its type isn't known.

    Searches every wiki/<type>/ directory. Returns None if no match.
    """
    for pt in PageType:
        candidate = page_path(paths, page_id, pt)
        if candidate.is_file():
            return candidate
    return None


def list_pages(
    paths: VaultPaths, page_type: PageType | None = None
) -> list[str]:
    """List page IDs (filenames sans .md) in the vault.

    If page_type is given, restrict to that type's directory.
    """
    dirs = (
        [_type_dir(paths, page_type)]
        if page_type is not None
        else [_type_dir(paths, pt) for pt in PageType]
    )
    ids: list[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix == ".md":
                ids.append(p.stem)
    return ids


def render_page_markdown(page: Page) -> str:
    """Serialize a Page to its on-disk markdown form."""
    fm = page.frontmatter_dict()
    yaml_text = yaml.safe_dump(
        fm,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    body = page.body.rstrip()
    # Ensure the body starts with a top-level heading matching the title.
    if not body.lstrip().startswith("# "):
        body = f"# {page.title}\n\n{body}".rstrip()
    return f"---\n{yaml_text}\n---\n\n{body}\n"


def _as_str_list(value) -> list[str]:
    """Coerce a YAML frontmatter value into a clean list of strings.

    Frontmatter is hand- and machine-edited, so list fields acquire two common
    YAML gotchas: a year-like tag (``- 1983``) parses as an ``int``, and a
    single unbracketed value (``tags: pythagorean``) parses as a bare scalar
    rather than a list. The strict ``Page`` model rejects both. Normalize them
    here — at the disk-read boundary where YAML typing happens — rather than
    crash the whole load over one numeric tag.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def parse_page_markdown(text: str, *, fallback_id: str = "") -> Page:
    """Parse a markdown file's text into a Page.

    Raises ValueError if frontmatter is missing or malformed.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("page is missing YAML frontmatter delimited by '---'")

    fm_text, body = match.group(1), match.group(2)
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise ValueError("frontmatter must be a YAML mapping")

    page_id = fm.get("id") or fallback_id
    if not page_id:
        raise ValueError("frontmatter is missing 'id'")

    type_value = fm.get("type")
    if type_value is None:
        raise ValueError(f"page {page_id!r} is missing 'type' in frontmatter")
    try:
        page_type = PageType(type_value)
    except ValueError as exc:
        raise ValueError(
            f"page {page_id!r} has unknown type {type_value!r}"
        ) from exc

    return Page(
        id=page_id,
        title=str(fm.get("title") or page_id),
        type=page_type,
        summary=str(fm.get("summary") or ""),
        tags=_as_str_list(fm.get("tags")),
        aliases=_as_str_list(fm.get("aliases")),
        sources=_as_str_list(fm.get("sources")),
        updated=str(fm.get("updated") or ""),
        # Evidence metadata: optional; empty string/list means unspecified
        # (the rule-based contradiction resolver in Mender will fall back
        # to its LLM branch when these aren't set).
        source_tier=str(fm.get("source_tier") or ""),
        confidence=str(fm.get("confidence") or ""),
        superseded_by=_as_str_list(fm.get("superseded_by")),
        body=body.strip(),
    )


def read_page(paths: VaultPaths, page_id: str) -> Page | None:
    """Read a page by ID. Returns None if the page doesn't exist."""
    path = locate_page(paths, page_id)
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    return parse_page_markdown(text, fallback_id=page_id)


def write_page(paths: VaultPaths, page: Page) -> Path:
    """Write (or overwrite) a page. Atomic via tmp-file + os.replace.

    Returns the final on-disk path.
    """
    if not page.updated:
        page = page.model_copy(update={"updated": today_iso()})

    target = page_path(paths, page.id, page.type)
    target.parent.mkdir(parents=True, exist_ok=True)

    text = render_page_markdown(page)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{page.id}.", suffix=".md.tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def page_exists(paths: VaultPaths, page_id: str) -> bool:
    return locate_page(paths, page_id) is not None
