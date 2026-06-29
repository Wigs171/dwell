"""Image/asset capture from article markdown.

Parses a saved source markdown file for image references and downloads
each image to `raw/assets/<source-slug>/`, so visual content (album
covers, diagrams, charts, pedalboard photos) is archived locally
rather than depending on the original URL's continued hosting.

The original markdown is NOT rewritten — downloads are additive.
Downstream consumers (a future image-aware PageWriter) can match
archived assets to the source by shared slug.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx


_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def parse_image_refs(markdown: str) -> list[tuple[str, str]]:
    """Return (alt_text, url) tuples from markdown image refs.

    Only http(s) URLs are returned — relative paths and data URIs are
    skipped. Markdown title syntax ( `![alt](url "title")` ) is
    stripped from the URL.
    """
    out: list[tuple[str, str]] = []
    for m in _IMG_REF_RE.finditer(markdown):
        alt = m.group(1).strip()
        raw = m.group(2).strip()
        # Split off an optional "title" suffix: `url "title"` or `url 'title'`
        url = raw.split(None, 1)[0] if raw else ""
        if not url.lower().startswith(("http://", "https://")):
            continue
        out.append((alt, url))
    return out


def download_assets(
    markdown: str,
    assets_dir: Path,
    *,
    max_per_source: int = 10,
    timeout: int = 30,
) -> list[Path]:
    """Download all image references in `markdown` to `assets_dir`.

    Returns the list of local paths actually written. Skips duplicates
    within a single source. Caps at `max_per_source` so a source with
    hundreds of embedded thumbnails doesn't flood the vault.
    """
    refs = parse_image_refs(markdown)[:max_per_source]
    if not refs:
        return []
    assets_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    seen_urls: set[str] = set()
    for alt, url in refs:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as http:
                resp = http.get(
                    url,
                    headers={"User-Agent": "CompendiumBuilder/0.1 (research agent)"},
                )
                resp.raise_for_status()
        except Exception:
            continue

        # Only keep content that actually looks like an image.
        content_type = resp.headers.get("content-type", "").lower()
        if not content_type.startswith("image/"):
            continue

        parsed = urlparse(url)
        stem = Path(parsed.path).stem or "asset"
        suffix = Path(parsed.path).suffix
        if not suffix:
            # Infer from Content-Type when the URL lacks an extension.
            if "jpeg" in content_type or "jpg" in content_type:
                suffix = ".jpg"
            elif "png" in content_type:
                suffix = ".png"
            elif "gif" in content_type:
                suffix = ".gif"
            elif "webp" in content_type:
                suffix = ".webp"
            elif "svg" in content_type:
                suffix = ".svg"
            else:
                suffix = ".img"
        target = assets_dir / f"{stem}{suffix}"
        n = 2
        while target.exists():
            target = assets_dir / f"{stem}-{n}{suffix}"
            n += 1
        try:
            target.write_bytes(resp.content)
            written.append(target)
        except Exception:
            continue
    return written
