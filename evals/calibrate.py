"""calibrate.py — score the JUDGES against the human gold set (labels.jsonl
from the review UI). Dev tooling only.

    python calibrate.py            # agreement per judge-model × rubric

For every labeled story, joins your per-criterion labels with each judged row
of the same file and reports: exact agreement (label == judge score), a
softer within-1 rate, and mean bias (judge − human; positive = judge is
lenient). Read it per criterion: a criterion with low agreement or heavy bias
is the next rubric fix — this is the measurement that makes judge optimization
falsifiable instead of vibes.
"""
from __future__ import annotations
import json, statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _rows(p: Path):
    if not p.exists():
        return []
    out = []
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def main() -> None:
    labels = {}
    for r in _rows(HERE / "labels.jsonl"):
        labels[r["file"]] = r          # last write wins
    if not labels:
        print("no labels yet — label stories in the review UI (:8092) first")
        return
    hist = _rows(HERE / "history.jsonl")
    # pairs[(model, rubric)][criterion] = list[(human, judge)]
    pairs: dict = defaultdict(lambda: defaultdict(list))
    for r in hist:
        L3 = r.get("L3") or {}
        st = L3.get("story") or {}
        lab = labels.get(r["file"])
        if not (st and lab):
            continue
        key = (L3.get("model", "?").split("-")[1], L3.get("rubric", "r?"))
        for crit, hv in (lab.get("scores") or {}).items():
            jv = st.get(crit)
            if isinstance(jv, int) and isinstance(hv, int):
                pairs[key][crit].append((hv, jv))
    if not pairs:
        print(f"{len(labels)} labeled file(s), but no judged rows share their "
              f"criteria yet")
        return
    for key in sorted(pairs):
        rows = pairs[key]
        n = sum(len(v) for v in rows.values())
        print(f"\n=== judge={key[0]} rubric={key[1]}  ({n} label-pairs, "
              f"{len(labels)} labeled files)")
        print(f"{'criterion':<20}{'n':>4}{'exact':>8}{'within1':>9}{'bias':>7}")
        allp = []
        for crit in sorted(rows):
            ps = rows[crit]
            allp += ps
            exact = sum(1 for h, j in ps if h == j) / len(ps)
            within = sum(1 for h, j in ps if abs(h - j) <= 1) / len(ps)
            bias = statistics.mean(j - h for h, j in ps)
            print(f"{crit:<20}{len(ps):>4}{exact:>8.0%}{within:>9.0%}{bias:>+7.2f}")
        exact = sum(1 for h, j in allp if h == j) / len(allp)
        bias = statistics.mean(j - h for h, j in allp)
        print(f"{'— overall':<20}{len(allp):>4}{exact:>8.0%}{'':>9}{bias:>+7.2f}")


if __name__ == "__main__":
    main()
