"""export_poles.py — precompute the HUMAN and FRONTIER-AI reference poles once,
so the stdlib-only review_server can draw the three-band point graph without
importing xgboost/pandas. Scores a fixed sample of the reference frame's own
vectors through the SAME joint-encode+model path the live fingerprints use, so
every point sits in one coordinate space.

    python dwell_bridge/export_poles.py            # -> evals/hl_poles.json
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # storyscope root
from dwell_bridge.hl_score import reference_poles, score_features

# where the dwell repo's evals live (humanlikeness.jsonl, stories/, poles):
# set DWELL_EVALS to your dwell checkout's evals/ directory.
import os as _os
EVALS = Path(_os.environ["DWELL_EVALS"]) if _os.environ.get("DWELL_EVALS")     else Path(__file__).resolve().parent.parent / "evals"
OUT = EVALS / "hl_poles.json"
BRIDGE = Path(__file__).resolve().parent


def main():
    poles = reference_poles(n_human=100, n_ai=100)
    # overlay OUR own frontier-AI control extractions (their Claude stories, Haiku
    # -scored through our pipeline) — the negative control that must pin ~0.00.
    ctrl_dir = BRIDGE / "ai_control_features"
    controls = []
    if ctrl_dir.exists():
        try:
            controls = [round(s.p_human, 4)
                        for s in score_features(ctrl_dir, top=1)]
        except Exception as e:
            print(f"[controls skipped: {str(e)[:80]}]")
    out = {
        "human": poles["human"],
        "ai": poles["ai"],
        "controls": sorted(controls),
        "human_mean": poles["human_all_mean"],
        "ai_mean": poles["ai_all_mean"],
        "n_human": len(poles["human"]),
        "n_ai": len(poles["ai"]),
        "n_controls": len(controls),
    }
    OUT.write_text(json.dumps(out), encoding="utf-8")
    print(f"human pole: n={out['n_human']} mean={out['human_mean']:.3f} "
          f"(sample median≈{poles['human'][len(poles['human'])//2]:.3f})")
    print(f"ai pole:    n={out['n_ai']} mean={out['ai_mean']:.3f}")
    print(f"controls:   n={out['n_controls']} "
          + (f"range {min(controls):.3f}-{max(controls):.3f}" if controls else ""))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
