"""diagnose.py — task 3: the fresh-corpus humanlikeness diagnosis.

  (a) fresh p25a distribution vs the old mixed-era archive (progress delta);
  (b) SHAP decomposition on the fresh corpus — rank OUR real tells with
      magnitudes, split page-local (next gate detector) vs planner-level (next
      prompt lever), and name the #1 of each;
  (c) staged-ON vs default sanity (same-seed pairs, n=6, directional only).

    python dwell_bridge/diagnose.py --fresh-dir dwell_bridge/fp_features/fresh_p25a \
        --archive-dir dwell_bridge/full_features

Reads humanlikeness.jsonl for the fresh rows (3a/3c) and the features dir for
full SHAP (3b). Joint-encode always; distribution not story-grades.
"""
from __future__ import annotations
import argparse, datetime, json, sys
from pathlib import Path

import numpy as np

BRIDGE = Path(__file__).resolve().parent
sys.path.insert(0, str(BRIDGE.parent))
from dwell_bridge.hl_score import (score_features, corpus_decomposition)  # noqa: E402

# where the dwell repo's evals live (humanlikeness.jsonl, stories/, poles):
# set DWELL_EVALS to your dwell checkout's evals/ directory.
import os as _os
EVALS = Path(_os.environ["DWELL_EVALS"]) if _os.environ.get("DWELL_EVALS")     else Path(__file__).resolve().parent.parent / "evals"
JSONL = EVALS / "humanlikeness.jsonl"


def dist(vals):
    a = np.array(vals, dtype=float)
    return dict(n=len(a), mean=a.mean(), median=float(np.median(a)),
               q1=float(np.percentile(a, 25)), q3=float(np.percentile(a, 75)),
               min=a.min(), max=a.max())


def _fmt(d):
    return (f"n={d['n']}  mean={d['mean']:.3f}  median={d['median']:.3f}  "
            f"IQR[{d['q1']:.2f},{d['q3']:.2f}]  range[{d['min']:.2f},{d['max']:.2f}]")


def load_fresh_rows(jsonl: Path, pv: str, date: str | None):
    rows = []
    if jsonl.exists():
        for l in jsonl.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("pv") == pv and (date is None or r.get("date") == date):
                rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh-dir", required=True, help="fresh corpus features dir (for SHAP)")
    ap.add_argument("--archive-dir", default=str(BRIDGE / "full_features"))
    ap.add_argument("--jsonl", default=str(JSONL))
    ap.add_argument("--pv", default="p25a")
    ap.add_argument("--date", default="", help="restrict fresh rows to this date (default: any)")
    a = ap.parse_args()

    print("=" * 72)
    print("TASK 3a — FRESH p25a  vs  OLD MIXED-ERA ARCHIVE   (P(human) distribution)")
    print("=" * 72)
    fresh_rows = load_fresh_rows(Path(a.jsonl), a.pv, a.date or None)
    fresh_p = [r["P_human"] for r in fresh_rows]
    # archive: score full_features (the n=46 mixed p10-p24 Haiku extractions)
    arch = score_features(a.archive_dir, top=1)
    arch_p = [s.p_human for s in arch]
    if fresh_p:
        fd, ad = dist(fresh_p), dist(arch_p)
        print(f"  FRESH  (p25a):  {_fmt(fd)}")
        print(f"  ARCHIVE(mixed): {_fmt(ad)}")
        dmean = fd['mean'] - ad['mean']
        dmed = fd['median'] - ad['median']
        print(f"  Δ mean = {dmean:+.3f}   Δ median = {dmed:+.3f}")
        verdict = ("the p20–p25a era measures MORE human" if dmean > 0.02 else
                   "the p20–p25a era measures LESS human" if dmean < -0.02 else
                   "no meaningful shift")
        print(f"  → {verdict} (distributional; boundary stories are rater-sensitive)")
    else:
        print("  no fresh p25a rows in jsonl yet — run the fingerprint pass first")

    print("\n" + "=" * 72)
    print("TASK 3b — SHAP DECOMPOSITION ON THE FRESH CORPUS  (our real top tells)")
    print("=" * 72)
    decomp = corpus_decomposition(a.fresh_dir)
    if decomp["n"] == 0:
        print("  no features in fresh-dir yet")
    else:
        rows = decomp["rows"]
        print(f"  (n={decomp['n']} stories; mean signed SHAP, most-negative = "
              f"strongest shared AI tell)\n")
        print(f"  {'rank':<5}{'signed':>8}{'|shap|':>8}{'n<0':>5}  {'kind':<8}{'feature'}")
        for i, r in enumerate(rows[:14], 1):
            print(f"  {i:<5}{r['mean_shap']:>+8.3f}{r['mean_abs']:>8.3f}"
                  f"{r['n_neg']:>5}  {r['kind']:<8}{r['fid']}  {r['name']}")
        page = next((r for r in rows if r["kind"] == "page" and r["mean_shap"] < 0), None)
        plan = next((r for r in rows if r["kind"] == "planner" and r["mean_shap"] < 0), None)
        print("\n  NEXT LEVERS:")
        if page:
            print(f"    #1 PAGE-LOCAL tell  → next GATE DETECTOR: {page['fid']} "
                  f"{page['name']}  ({page['mean_shap']:+.3f})")
        if plan:
            print(f"    #1 PLANNER-LEVEL tell → next PROMPT LEVER: {plan['fid']} "
                  f"{plan['name']}  ({plan['mean_shap']:+.3f})")

    print("\n" + "=" * 72)
    print("TASK 3c — STAGED-ON vs DEFAULT  (same-seed pairs, n<=6, DIRECTIONAL only)")
    print("=" * 72)
    by_name = {r["file"]: r for r in fresh_rows}
    pairs = []
    for r in fresh_rows:
        if r.get("staged"):
            base = r["file"].replace("_stg.md", ".md")
            if base in by_name:
                pairs.append((base, by_name[base]["P_human"], r["P_human"]))
    if pairs:
        print(f"  {'config':<48}{'single':>8}{'staged':>8}{'Δ':>8}")
        deltas = []
        for base, sp, st in sorted(pairs):
            d = st - sp
            deltas.append(d)
            print(f"  {base[:46]:<48}{sp:>8.3f}{st:>8.3f}{d:>+8.3f}")
        md = float(np.mean(deltas))
        print(f"\n  mean Δ (staged − single) = {md:+.3f}  (n={len(deltas)})")
        print(f"  → staged {'raises' if md>0 else 'lowers' if md<0 else 'does not move'}"
              f" measured humanlikeness on these pairs (directional, n small)")
    else:
        print("  no staged/default pairs found in fresh rows yet")


if __name__ == "__main__":
    main()
