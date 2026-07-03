"""Export a Dwell vault as an OKF bundle (DWELL_OKF.md step 5).

Flat directory of Markdown concepts: `[[slug]]` → `[Title](slug.md)`, frontmatter
mapped (summary→description, updated→timestamp; type kept, extras preserved —
OKF is minimally opinionated). Ghost wikilinks stay as dangling .md links, so
re-importing the bundle keeps the frontier intact. `_meta/`, `raw/`, CLAUDE.md
are Dwell-side and stay behind.

Usage: python okf_export.py "<vault dir>" ["<dest dir>"]
"""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from compendium.vault.layout import VaultPaths                      # noqa: E402
from compendium.vault.pages import list_pages, read_page           # noqa: E402

WIKI = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]")


def export(vault_dir: Path, dest: Path) -> tuple[int, int]:
    vp = VaultPaths(vault_dir)
    ids = list_pages(vp)
    pages = {}
    for pid in ids:
        try:
            pg = read_page(vp, pid)
            if pg is not None:
                pages[pid] = pg
        except Exception:
            pass
    titles = {pid: (pg.title or pid) for pid, pg in pages.items()}

    def rewrite(m: re.Match) -> str:
        tgt = m.group(1).strip()
        label = m.group(2) or titles.get(tgt.lower(), tgt.replace("-", " "))
        return f"[{label}]({tgt.lower()}.md)"

    dest.mkdir(parents=True, exist_ok=True)
    n_links = 0
    for pid, pg in pages.items():
        body, k = WIKI.subn(rewrite, pg.body)
        n_links += k
        fm = [f"type: {pg.type.value}", f"title: {pg.title}"]
        if pg.summary:
            fm.append(f"description: {pg.summary!r}")
        if pg.tags:
            fm.append(f"tags: [{', '.join(pg.tags)}]")
        if pg.updated:
            fm.append(f"timestamp: {pg.updated}")
        if pg.aliases:
            fm.append(f"aliases: [{', '.join(pg.aliases)}]")
        if pg.sources:
            fm.append(f"sources: [{', '.join(pg.sources)}]")
        io.open(dest / f"{pid}.md", "w", encoding="utf-8").write(
            "---\n" + "\n".join(fm) + "\n---\n\n" + body.strip() + "\n")
    # index.md — OKF-conventional entry point
    lines = [f"# {vault_dir.name}", "",
             "An OKF bundle exported from a Dwell vault.", ""]
    lines += [f"- [{titles[p]}]({p}.md)" for p in sorted(pages)]
    io.open(dest / "index.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return len(pages), n_links


if __name__ == "__main__":
    v = Path(sys.argv[1])
    d = Path(sys.argv[2]) if len(sys.argv) > 2 else v.parent / (v.name + "-okf")
    n, k = export(v, d)
    print(f"exported {n} concepts, rewrote {k} links -> {d}")
