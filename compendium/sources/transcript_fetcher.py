"""Transcript capture for video/podcast sources.

YouTube is the primary supported provider (via youtube-transcript-api).
Other providers can be bolted on later — they each have their own
idiosyncratic surfaces (podcasts with RSS transcript URLs, Spotify
transcripts, etc).
"""

from __future__ import annotations

import re


_YT_ID_RE = re.compile(
    r"(?:"
    r"(?:youtube\.com/(?:watch\?.*?v=|embed/|v/|shorts/))|"
    r"(?:youtu\.be/)"
    r")([A-Za-z0-9_-]{11})"
)
_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


def is_youtube_url(url: str) -> bool:
    if not url:
        return False
    lower = url.lower()
    return any(host in lower for host in _YT_HOSTS)


def extract_video_id(url: str) -> str | None:
    """Pull the 11-character YouTube video ID out of a URL."""
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def fetch_youtube_transcript(
    url: str, *, languages: list[str] | None = None
) -> str:
    """Return a timestamped transcript for a YouTube URL.

    Empty string if the video has no transcript, youtube-transcript-api
    isn't installed, or an error occurred.

    The transcript is formatted as one line per caption entry:
        [00:01:23] caption text here

    Timestamps use HH:MM:SS when the video exceeds an hour, otherwise
    MM:SS. This makes the transcript greppable and citeable.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""

    langs = languages or ["en"]
    try:
        api = YouTubeTranscriptApi()
        entries = api.fetch(video_id, languages=langs)
        # youtube_transcript_api returns a FetchedTranscript (iterable of
        # FetchedTranscriptSnippet objects with .text/.start/.duration).
        raw_items = list(entries)
    except Exception:
        return ""

    if not raw_items:
        return ""

    # Determine whether to use HH:MM:SS by checking the last entry's start.
    last_start = 0
    for item in raw_items:
        start = _extract_start(item)
        if start > last_start:
            last_start = start
    use_hours = last_start >= 3600

    lines: list[str] = []
    for item in raw_items:
        start = int(_extract_start(item))
        text = _extract_text(item).strip()
        if not text:
            continue
        if use_hours:
            hh, rem = divmod(start, 3600)
            mm, ss = divmod(rem, 60)
            ts = f"{hh:02d}:{mm:02d}:{ss:02d}"
        else:
            mm, ss = divmod(start, 60)
            ts = f"{mm:02d}:{ss:02d}"
        lines.append(f"[{ts}] {text}")
    return "\n".join(lines)


def _extract_start(item) -> float:
    """Support both dict-style (older API) and object-style entries."""
    if isinstance(item, dict):
        return float(item.get("start", 0))
    return float(getattr(item, "start", 0))


def _extract_text(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(getattr(item, "text", ""))
