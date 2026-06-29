"""log.md append-only event record.

Every operation appends a single entry with the canonical prefix

    ## [YYYY-MM-DD] <op> | <subject>

so the log stays greppable:

    grep "^## \\[" log.md | tail -5

The body of each entry is free-form markdown: bullets, details, numbers.
"""

from __future__ import annotations

from datetime import datetime

from compendium.vault.layout import VaultPaths
from compendium.vault.pages import today_iso


_PREFIX_TEMPLATE = "## [{date}] {op} | {subject}"


def _ensure_log(paths: VaultPaths) -> None:
    if not paths.log_md.exists():
        paths.log_md.write_text("# Log\n\n", encoding="utf-8")


def append_entry(
    paths: VaultPaths,
    *,
    op: str,
    subject: str,
    body: str = "",
    date_str: str | None = None,
) -> None:
    """Append one entry. `body` is appended below the heading with a blank
    line between heading and body. A trailing blank line is always added
    so successive entries stay visually separated.
    """
    _ensure_log(paths)
    heading = _PREFIX_TEMPLATE.format(
        date=date_str or today_iso(), op=op, subject=subject
    )
    block = heading + "\n"
    if body:
        block += "\n" + body.rstrip() + "\n"
    block += "\n"
    with paths.log_md.open("a", encoding="utf-8", newline="\n") as f:
        f.write(block)


def read_recent(paths: VaultPaths, n: int = 5) -> str:
    """Return the last `n` log entries as a single string.

    Useful as cheap context for the LLM: what happened recently in this
    vault. Returns an empty string if the log is empty.
    """
    if not paths.log_md.exists():
        return ""
    text = paths.log_md.read_text(encoding="utf-8")
    entries: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ["):
            if current:
                entries.append("\n".join(current).rstrip())
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append("\n".join(current).rstrip())
    return "\n\n".join(entries[-n:]) if entries else ""


def timestamp_iso() -> str:
    """ISO-8601 timestamp to the second. Used in IngestReport and similar."""
    return datetime.now().replace(microsecond=0).isoformat()
