"""fingerprint.py — the post-generation humanlikeness fingerprint.

Every Dwell story -> Haiku feature extraction (StoryScope's own pipeline, pinned
rater claude-haiku-4-5-20251001) -> joint-encode vs the reference parquet ->
P(human) + top SHAP tells -> one line appended to evals/humanlikeness.jsonl.

    python dwell_bridge/fingerprint.py <story.md> [<story2.md> ...]
    python dwell_bridge/fingerprint.py --glob "path/to/path_test_*.md"
    python dwell_bridge/fingerprint.py --dir <features_dir_already_extracted>  # score-only

This is a STORY-LEVEL instrument (needs the whole story); it is NOT wired into
per-page serving. Wire it as a post-sweep pass instead. The jsonl is the point
graph's data source.

Laws honored: joint-encode always (hl_score); pinned Haiku rater; distribution
not story-grades. Extraction is ~10 Haiku calls/story (~$0.015).
"""
from __future__ import annotations
import argparse, csv, datetime, glob as _glob, json, os, re, subprocess, sys, tempfile
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
SS_ROOT = BRIDGE.parent                      # storyscope repo root
sys.path.insert(0, str(SS_ROOT))

# where the dwell repo's evals live (humanlikeness.jsonl, stories/, poles):
# set DWELL_EVALS to your dwell checkout's evals/ directory.
import os as _os
EVALS = Path(_os.environ["DWELL_EVALS"]) if _os.environ.get("DWELL_EVALS")     else Path(__file__).resolve().parent.parent / "evals"
JSONL = EVALS / "humanlikeness.jsonl"
DOTENV = EVALS.parent / ".env"

PAGE_RE = re.compile(r"^## page (\d+) · (\w+) · (.+?) · (\d+)w\s*$", re.M)
HAIKU = "claude-haiku-4-5-20251001"


def _dotenv_key(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if DOTENV.exists():
        for line in DOTENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def story_body(path: Path) -> str:
    """Page bodies joined; plan headers / GATE lines / trailers stripped — the
    exact shape export_corpus.py feeds the extractor, so scores stay comparable
    with the archive."""
    text = path.read_text(encoding="utf-8", errors="replace")
    heads = list(PAGE_RE.finditer(text))
    if len(heads) < 4:
        return ""
    bodies = []
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        b = text[h.end():end].split("\npages=")[0]
        b = re.sub(r"\n---\s*$", "", b)
        b = "\n".join(l for l in b.splitlines() if not l.startswith("GATE "))
        bodies.append(b.strip())
    return "\n\n".join(bodies).strip()


def meta_of(path: Path, body: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    pv = (re.search(r"^PV:\s*(\S+)", text, re.M) or [None, "?"])[1]
    staged = bool(re.search(r"^STAGED:\s*1", text, re.M)) or "_stg" in path.name
    return {"file": path.name, "pv": pv, "staged": staged,
            "words": len(body.split())}


def is_enacted(name: str) -> bool:
    # the humanlikeness feature space is narrative; skip didactic/control/auth
    return not any(t in name for t in ("tutorial", "guided", "_qa", "_brief",
                                       "control", "_auth"))


def extract(files: list[Path], feat_dir: Path) -> list[dict]:
    """Build a CSV of the (enacted, >=1200w) stories and run Haiku extraction."""
    rows, metas = [], []
    for i, f in enumerate(sorted(files), 1):
        if not is_enacted(f.name):
            print(f"  skip (didactic/control): {f.name}")
            continue
        body = story_body(f)
        m = meta_of(f, body)
        if m["words"] < 1200:
            print(f"  skip (<1200w): {f.name} ({m['words']}w)")
            continue
        rows.append({"prompt_id": i, "title": f.stem, "human_story": body})
        metas.append(m)
    if not rows:
        return []
    feat_dir.mkdir(parents=True, exist_ok=True)
    csv_path = feat_dir / "batch.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["prompt_id", "title", "human_story"])
        w.writeheader(); w.writerows(rows)
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               ANTHROPIC_API_KEY=_dotenv_key("ANTHROPIC_API_KEY"))
    cmd = [sys.executable, "-m", "storyscope.5_feature_application.apply_features",
           "--csv", str(csv_path), "--taxonomy", str(SS_ROOT / "data" / "taxonomy.json"),
           "--output-dir", str(feat_dir), "--provider", "anthropic", "--model", HAIKU,
           "--parallel", "2", "--dim-workers", "4", "--sources", "human", "--resume"]
    print(f"  extracting {len(rows)} stories with {HAIKU} ...", flush=True)
    subprocess.run(cmd, cwd=str(SS_ROOT), env=env, check=False)
    return metas


def fingerprint(files: list[Path], feat_dir: Path, jsonl: Path,
                top: int = 6) -> list[dict]:
    metas = extract(files, feat_dir)
    if not metas:
        print("  nothing to fingerprint")
        return []
    from dwell_bridge.hl_score import score_features
    scores = score_features(feat_dir, top=top)
    by_title = {s.title: s for s in scores}
    today = datetime.date.today().isoformat()
    written = []
    with open(jsonl, "a", encoding="utf-8") as fh:
        for m in metas:
            s = by_title.get(Path(m["file"]).stem)
            if s is None:
                print(f"  !! no score for {m['file']} (extraction failed?)")
                continue
            row = {"file": m["file"], "pv": m["pv"], "date": today,
                   "staged": m["staged"], "words": m["words"],
                   "P_human": round(s.p_human, 4),
                   "tells": [{"fid": t.fid, "name": t.name, "shap": t.shap}
                             for t in s.tells[:top]]}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            written.append(row)
            print(f"  P(human)={row['P_human']:.3f}  {m['file']}"
                  f"{'  [staged]' if m['staged'] else ''}")
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--glob", default="")
    ap.add_argument("--feat-dir", default="", help="features output dir (default: timestamped under fp_features/)")
    ap.add_argument("--jsonl", default=str(JSONL))
    ap.add_argument("--top", type=int, default=6)
    a = ap.parse_args()
    files = [Path(f) for f in a.files]
    if a.glob:
        files += [Path(f) for f in _glob.glob(a.glob)]
    files = [f for f in files if f.exists()]
    if not files:
        sys.exit("no story files given (positional paths or --glob)")
    feat_dir = Path(a.feat_dir) if a.feat_dir else (
        BRIDGE / "fp_features" / datetime.datetime.now().strftime("run_%Y%m%d_%H%M%S"))
    rows = fingerprint(files, feat_dir, Path(a.jsonl), a.top)
    if rows:
        import numpy as np
        arr = np.array([r["P_human"] for r in rows])
        print(f"\nfingerprinted {len(rows)}  mean P(human)={arr.mean():.3f} "
              f"median={np.median(arr):.3f}  -> {a.jsonl}")


if __name__ == "__main__":
    main()
