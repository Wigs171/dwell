"""Backfill raw/.ingest-registry.json for vaults ingested before the registry existed.

Evidence: every ingest writes a summary page under wiki/sources/. A raw file whose
normalized stem matches a sources-page id (exact, or an unambiguous prefix) was
ingested; hash it and record it. Everything unmatched stays out of the registry —
i.e. becomes 'pending' in the vault UI — so the vault card shows the true remaining
work (e.g. MPH's un-ingested transcripts).

Usage:
  python backfill_registry.py            # dry run, all vaults under ~/Dwell
  python backfill_registry.py --apply    # write registries
  python backfill_registry.py --apply "My Vault Name"   # one vault
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from compendium.vault.layout import VaultPaths                      # noqa: E402
from compendium.vault.registry import (                             # noqa: E402
    IngestRegistry, RegistryEntry, hash_file, now_iso)

VAULT_ROOT = Path.home() / "Dwell"
SKIP_DIRS = {"assets", "uploads"}
SKIP_SUFFIX = (".claude-baseline", ".extracted.txt")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def backfill(vault_dir: Path, apply: bool) -> None:
    vp = VaultPaths(vault_dir)
    if not vp.sources.is_dir():
        print(f"{vault_dir.name}: no wiki/sources/ — skipped")
        return
    source_ids = {p.stem for p in vp.sources.glob("*.md")}
    by_norm = {norm(s): s for s in source_ids}
    reg = IngestRegistry(vp)
    already = set()
    try:
        import json
        rj = json.loads((vp.raw / ".ingest-registry.json").read_text(encoding="utf-8"))
        already = {e.get("source_id") for e in rj.get("entries", [])}
    except Exception:
        pass

    matched, unmatched, kept = [], [], 0
    for sub in sorted(p for p in vp.raw.iterdir() if p.is_dir()):
        if sub.name in SKIP_DIRS:
            continue
        for f in sorted(sub.iterdir()):
            if (not f.is_file() or f.name.startswith(".")
                    or f.name.lower().endswith(SKIP_SUFFIX)):
                continue
            n = norm(f.stem)
            sid = by_norm.get(n)
            if sid is None:  # unambiguous prefix match (chapter files, trimmed titles)
                hits = [s for k, s in by_norm.items()
                        if len(n) >= 8 and (k.startswith(n) or n.startswith(k))]
                sid = hits[0] if len(hits) == 1 else None
            if sid is None:
                unmatched.append(f"{sub.name}/{f.name}")
                continue
            if sid in already:
                kept += 1
                continue
            matched.append((sid, f))

    print(f"{vault_dir.name}: {len(matched)} to record, {kept} already recorded, "
          f"{len(unmatched)} unmatched (stay pending)")
    if unmatched[:4]:
        print("   e.g. pending:", ", ".join(unmatched[:4]))
    if apply:
        for sid, f in matched:
            reg.record(RegistryEntry(
                source_id=sid,
                raw_path=str(f.relative_to(vault_dir)).replace("\\", "/"),
                ingested=now_iso(), hash=hash_file(f),
                origin="backfill:sources-page-match",
                extras={"backfilled": True}))
        print(f"   -> wrote {len(matched)} entries")


def main() -> None:
    apply = "--apply" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    dirs = ([VAULT_ROOT / n for n in names] if names
            else sorted(p for p in VAULT_ROOT.iterdir() if p.is_dir()))
    for d in dirs:
        if (d / "CLAUDE.md").is_file():
            backfill(d, apply)


if __name__ == "__main__":
    main()
