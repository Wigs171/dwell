"""hl_score.py — the shared humanlikeness instrument: features dir -> P(human)
+ per-feature SHAP decomposition.

Reuses the EXACT joint-encode discipline baked into score_humanlikeness.py (the
$49 lesson: categorical encoding is DATA-DEPENDENT, so we ALWAYS encode jointly
with the reference training parquet and slice off our rows — never a small batch
alone). Adds XGBoost-native SHAP (booster.predict(..., pred_contribs=True), no
`shap` dependency): the model predicts human=1, so a NEGATIVE contribution pushes
a story toward the frontier-AI cluster — i.e. it is an "AI tell". We rank tells
by that signed contribution.

Library:
    scored = score_features(features_dir)         # -> list[StoryScore]
    poles  = reference_poles(n_human=100)         # -> {"human":[...], "ai":[...]}
Both go through one shared joint-encode so all points are comparable.

CLI:
    python dwell_bridge/hl_score.py <features_dir> [--top 8]
"""
from __future__ import annotations
import argparse, sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storyscope.utils.feature_encoder import (          # noqa: E402
    load_taxonomy, build_feature_type_map, get_taxonomy_feature_ids,
    load_features_matrix, load_features_parquet, encode_features,
    friendly_col_name)

TAXONOMY = ROOT / "data" / "taxonomy.json"
REF_PARQUET = ROOT / "data" / "storyscope_features.parquet"
# The retrained, joint-encode-native classifier (462 multi_hot cols, F1 .958).
MODEL = Path(__file__).resolve().parent / "models" / "binary" / "model.json"


@dataclass
class Tell:
    fid: str
    name: str
    value: str
    shap: float          # signed, margin space; <0 = pushes toward AI


@dataclass
class StoryScore:
    title: str
    p_human: float
    tells: list[Tell] = field(default_factory=list)     # most-negative first (AI tells)
    boosts: list[Tell] = field(default_factory=list)     # most-positive first (human pulls)


_CACHE: dict = {}


def _load_shared():
    """taxonomy, feature-type map, feature ids, and the reference frame — cached
    (the parquet is a few MB; re-reading it per call is wasteful)."""
    if "big" not in _CACHE:
        tax = load_taxonomy(str(TAXONOMY))
        ftm = build_feature_type_map(tax)
        fids = get_taxonomy_feature_ids(tax)
        big, _, _ = load_features_parquet(str(REF_PARQUET), tax)
        _CACHE.update(tax=tax, ftm=ftm, fids=fids, big=big)
    return _CACHE["tax"], _CACHE["ftm"], _CACHE["fids"], _CACHE["big"]


def _model_and_booster(model_path: Path = MODEL):
    from xgboost import XGBClassifier
    clf = XGBClassifier()
    mp = Path(model_path)
    if not mp.exists():
        mp = ROOT / "data" / "models" / "binary_narrative.json"
    clf.load_model(str(mp))
    return clf, clf.get_booster()


def _encode_joint(df: pd.DataFrame, fids, big) -> pd.DataFrame:
    """Joint-encode df's rows against the reference frame, slice ours back off."""
    combo = pd.concat([big[fids], df[fids]], ignore_index=True)
    X, cols = encode_features(combo, fids, _CACHE["ftm"] if "ftm" in _CACHE else None,
                              mode="multi_hot")
    X = X[len(big):]
    return pd.DataFrame(X, columns=cols)


def _align(Xdf: pd.DataFrame, booster) -> tuple[pd.DataFrame, list[str]]:
    want = booster.feature_names
    if want:
        for c in [c for c in want if c not in Xdf.columns]:
            Xdf[c] = np.nan
        Xdf = Xdf[want]
    return Xdf, (want or list(Xdf.columns))


def _decompose(Xdf: pd.DataFrame, want, booster, df, ftm, top: int):
    """Per-row SHAP -> aggregate by feature id, split into tells vs boosts."""
    import xgboost as xgb
    dm = xgb.DMatrix(Xdf.values, feature_names=want, missing=np.nan)
    contribs = booster.predict(dm, pred_contribs=True)      # (n, len(want)+1); last=bias
    # base feature-id per encoded column, and a display value for it
    fid_of = [c.rsplit("__", 1)[0] if "__" in c else c for c in want]
    out = []
    for i in range(len(Xdf)):
        agg: dict[str, float] = {}
        for j, c in enumerate(want):
            v = contribs[i, j]
            if v:
                agg[fid_of[j]] = agg.get(fid_of[j], 0.0) + float(v)
        title = df.iloc[i]["story_title"]
        def _mk(fid, s):
            name = ftm.get(fid, {}).get("name", fid) if ftm else fid
            val = ""
            if fid in df.columns:
                val = str(df.iloc[i][fid])[:60]
            return Tell(fid=fid, name=name, value=val, shap=round(s, 4))
        ranked = sorted(agg.items(), key=lambda kv: kv[1])
        tells = [_mk(f, s) for f, s in ranked if s < 0][:top]
        boosts = [_mk(f, s) for f, s in reversed(ranked) if s > 0][:top]
        out.append((title, tells, boosts))
    return out


def score_features(features_dir: str | Path, model_path: Path = MODEL,
                   top: int = 8) -> list[StoryScore]:
    tax, ftm, fids, big = _load_shared()
    df, _, authors = load_features_matrix(str(features_dir), tax)
    if len(df) == 0:
        return []
    Xdf = _encode_joint(df, fids, big)
    clf, booster = _model_and_booster(model_path)
    Xdf, want = _align(Xdf, booster)
    proba = clf.predict_proba(Xdf.values)[:, 1]              # human = 1
    decomp = _decompose(Xdf, want, booster, df, ftm, top)
    scores = []
    for i, (title, tells, boosts) in enumerate(decomp):
        scores.append(StoryScore(title=title, p_human=float(proba[i]),
                                  tells=tells, boosts=boosts))
    # keep a stable order matching df
    return scores


def reference_poles(n_human: int = 100, n_ai: int = 100, seed: int = 7) -> dict:
    """Score a fixed sample of the reference frame's own vectors through the same
    joint-encode+model path, so the HUMAN pole (~0.99) and FRONTIER-AI pole
    (~0.00) sit in the same coordinate space as Dwell points. Deterministic."""
    tax, ftm, fids, big = _load_shared()
    clf, booster = _model_and_booster()
    # encode the whole reference frame against itself (self-consistent codes)
    X, cols = encode_features(big[fids], fids, ftm, mode="multi_hot")
    Xdf = pd.DataFrame(X, columns=cols)
    Xdf, want = _align(Xdf, booster)
    proba = clf.predict_proba(Xdf.values)[:, 1]
    author = big["author"].astype(str).values
    rng = np.random.RandomState(seed)
    def _sample(mask, k):
        idx = np.where(mask)[0]
        if len(idx) > k:
            idx = rng.choice(idx, k, replace=False)
        return sorted(float(proba[j]) for j in idx)
    human_mask = author == "human"
    ai_mask = ~human_mask
    return {"human": _sample(human_mask, n_human),
            "ai": _sample(ai_mask, n_ai),
            "human_all_mean": float(proba[human_mask].mean()),
            "ai_all_mean": float(proba[ai_mask].mean())}


# feature-id prefix -> whether the tell is fixable PAGE-LOCALLY (prose surface,
# reachable by an in-paint repair) or is a PLANNER-LEVEL structural choice
# (arc / plot / time / revelation — reachable only by a prompt lever). Used to
# route the #1 tell of each kind to the right fix (task 3b).
_PAGE_LOCAL_PREFIX = ("STY", "SET_ATM", "SIT_MET", "SIT_TON", "SOC_DIA", "PSP")
_PLANNER_PREFIX = ("EVT", "PLT", "STR", "TMP", "REV", "SIT_CON")


def tell_kind(fid: str) -> str:
    if fid.startswith(("SET_ATM", "SIT_MET", "SIT_TON")):
        return "page"
    if any(fid.startswith(p) for p in ("STY", "PSP", "SOC_DIA")):
        return "page"
    if any(fid.startswith(p) for p in _PLANNER_PREFIX):
        return "planner"
    return "other"


def corpus_decomposition(features_dir: str | Path,
                         model_path: Path = MODEL) -> dict:
    """FULL per-feature SHAP over the whole corpus (not truncated to per-story
    top-N) — the true global ranking of what pushes this corpus toward the
    frontier-AI cluster. Returns rows sorted most-negative (strongest shared AI
    tell) first, each: {fid, name, kind, mean_shap, mean_abs, n_neg}."""
    import xgboost as xgb
    tax, ftm, fids, big = _load_shared()
    df, _, _ = load_features_matrix(str(features_dir), tax)
    if len(df) == 0:
        return {"n": 0, "rows": []}
    Xdf = _encode_joint(df, fids, big)
    clf, booster = _model_and_booster(model_path)
    Xdf, want = _align(Xdf, booster)
    proba = clf.predict_proba(Xdf.values)[:, 1]
    dm = xgb.DMatrix(Xdf.values, feature_names=want, missing=np.nan)
    contribs = booster.predict(dm, pred_contribs=True)          # (n, len(want)+1)
    fid_of = [c.rsplit("__", 1)[0] if "__" in c else c for c in want]
    # aggregate per story to feature level, then average across stories
    per_story = np.zeros((len(df), len(set(fid_of))))
    uniq = sorted(set(fid_of))
    col_ix = {f: i for i, f in enumerate(uniq)}
    for j, f in enumerate(fid_of):
        per_story[:, col_ix[f]] += contribs[:, j]
    mean_shap = per_story.mean(axis=0)
    mean_abs = np.abs(per_story).mean(axis=0)
    n_neg = (per_story < 0).sum(axis=0)
    rows = []
    for f in uniq:
        i = col_ix[f]
        rows.append({"fid": f, "name": ftm.get(f, {}).get("name", f),
                     "kind": tell_kind(f), "mean_shap": round(float(mean_shap[i]), 4),
                     "mean_abs": round(float(mean_abs[i]), 4),
                     "n_neg": int(n_neg[i])})
    rows.sort(key=lambda r: r["mean_shap"])
    return {"n": len(df), "p_human": [float(p) for p in proba], "rows": rows}


def corpus_shap(scores: list[StoryScore], top: int = 15) -> list[tuple[str, str, float]]:
    """Mean signed SHAP per feature across a corpus -> (fid, name, mean_shap),
    most-negative (strongest shared AI tell) first."""
    agg: dict[str, list] = {}
    names: dict[str, str] = {}
    for s in scores:
        for t in list(s.tells) + list(s.boosts):
            agg.setdefault(t.fid, []).append(t.shap)
            names[t.fid] = t.name
    rows = [(fid, names[fid], float(np.mean(v))) for fid, v in agg.items()]
    rows.sort(key=lambda r: r[2])
    return rows[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("features_dir")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--model", default=str(MODEL))
    a = ap.parse_args()
    scores = score_features(a.features_dir, Path(a.model), a.top)
    scores_sorted = sorted(scores, key=lambda s: s.p_human)
    print(f"\nP(human) per story  (n={len(scores)})")
    for s in scores_sorted:
        print(f"  {s.p_human:.3f}  {s.title}")
    arr = np.array([s.p_human for s in scores])
    print(f"\nmean={arr.mean():.3f} median={np.median(arr):.3f} "
          f"min={arr.min():.3f} max={arr.max():.3f}")
    print("\nTop shared AI-tells (mean signed SHAP, most negative = strongest tell):")
    for fid, name, ms in corpus_shap(scores, a.top):
        arrow = "AI " if ms < 0 else "hum"
        print(f"  [{arrow}] {ms:+.3f}  {fid}  {name}")


if __name__ == "__main__":
    main()
