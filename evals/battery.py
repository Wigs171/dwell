"""battery.py — run the fixed eval battery / sweeps and show the trend across
prompt versions. Dev tooling only (no app/UI wiring).

    python battery.py --trend                 # per-PV means from history.jsonl
    python battery.py --run [--judge]         # generate + score the battery
    python battery.py --score-only G [...]    # score existing files (globs ok)
    python battery.py --sweep 20 [--judge]    # stratified random exam

Vaults are machine-local: copy vaults.example.json to vaults.local.json
(gitignored) and point the keys at your own vault folders. The battery holds
(vault-key, seed, dream, form[, {spine, goal}]) tuples — the optional dict is
an AUTHORED path (the tutorial product shape: an expert picks the nodes and
states the training goal). Scores are plan-relative and the planner is
nondeterministic: ~5 rows per PV before trusting a movement.
"""
from __future__ import annotations
import argparse, json, os, statistics, subprocess, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
HIST = HERE / "history.jsonl"
GEN_DEFAULT = HERE / "gen_path.py"
# the humanlikeness fingerprint (StoryScope bridge) — an optional post-generation
# pass, story-level by design (never per-page). Point DWELL_FINGERPRINT_SCRIPT at
# your bridge's fingerprint.py (see storyscope_bridge/README.md); unset = skip.
FINGERPRINT = Path(os.environ.get("DWELL_FINGERPRINT_SCRIPT", ""))


def _fingerprint_pass(files: list) -> None:
    files = [f for f in files if f and Path(f).exists()]
    if not files:
        return
    if not os.environ.get("DWELL_FINGERPRINT_SCRIPT") or not FINGERPRINT.exists():
        print("[fingerprint skipped: set DWELL_FINGERPRINT_SCRIPT to the bridge script]")
        return
    print(f"== fingerprint pass: {len(files)} stories -> humanlikeness.jsonl", flush=True)
    subprocess.run([sys.executable, str(FINGERPRINT), *[str(f) for f in files]],
                   check=False, env=dict(os.environ, PYTHONIOENCODING="utf-8"))


def load_vaults() -> dict[str, str]:
    for name in ("vaults.local.json", "vaults.example.json"):
        p = HERE / name
        if p.exists():
            try:
                return {k: v for k, v in json.loads(
                    p.read_text(encoding="utf-8")).items() if Path(v).exists()}
            except Exception as e:
                print(f"[{name}: {e}]", file=sys.stderr)
    return {}


VAULTS = load_vaults()

# Edit to taste — keys must exist in your vaults.local.json; missing keys skip.
BATTERY: list[tuple] = [
    ("fiction", 6, 0.85, "story"),
    ("fiction", 3, 0.5, "story"),
    ("lore", 11, 0.85, "story"),
    ("fiction", 6, 0.5, "tutorial"),
]

KEYS = ["L1.slop_hits", "L1.meta_hits", "L1.clone_max", "L1.opening_dupes",
        "L1.mood_leaks", "L1.prot_presence", "L1.cast_present",
        "L2.mood_match", "L2.valley_in_fall", "L3.seam_connected",
        "L3.judge_score"]


def _get(row: dict, dotted: str):
    cur = row
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if isinstance(cur, bool):
        return 1.0 if cur else 0.0
    if isinstance(cur, list):
        return float(len(cur))
    return float(cur) if isinstance(cur, (int, float)) else None


def trend() -> None:
    if not HIST.exists():
        print("no history yet")
        return
    rows = []
    for l in HIST.read_text(encoding="utf-8").splitlines():
        if l.strip():
            try:
                rows.append(json.loads(l))
            except json.JSONDecodeError:
                print(f"[skipping malformed history line: {l[:60]}…]", file=sys.stderr)
    by_pv: dict = defaultdict(list)
    for r in rows:
        by_pv[r.get("pv", "?")].append(r)
    pvs = sorted(by_pv, key=lambda p: (len(p), p))
    print(f"{'metric':<22}" + "".join(f"{pv:>10}" for pv in pvs))
    print(f"{'  (rows)':<22}" + "".join(f"{len(by_pv[pv]):>10}" for pv in pvs))
    for key in KEYS:
        cells = []
        for pv in pvs:
            vals = [v for r in by_pv[pv] if (v := _get(r, key)) is not None]
            cells.append(f"{statistics.mean(vals):>10.2f}" if vals else f"{'—':>10}")
        print(f"{key:<22}" + "".join(cells))


def score(f: Path, vault: str, judge: bool, form: str = "") -> None:
    try:
        import shutil
        (HERE / "stories").mkdir(exist_ok=True)
        shutil.copy2(f, HERE / "stories" / Path(f).name)
    except Exception:
        pass
    cmd = [sys.executable, str(HERE / "score_story.py"), str(f)]
    if vault:
        cmd += ["--vault", vault]
    if judge:
        cmd.append("--judge")
    if form:
        cmd += ["--form", form]
    subprocess.run(cmd, check=False, env=dict(os.environ, PYTHONIOENCODING="utf-8"))


def _outname(vault: str, seed: int, dream: float, form: str, authored: bool) -> str:
    vtag = Path(vault).name.split()[0].lower()
    dtag = str(dream).replace(".", "")
    ftag = "" if form == "story" else f"_{form}"
    return f"path_test_{vtag}gen_{seed}_d{dtag}{ftag}{'_auth' if authored else ''}.md"


def run_cfg(cfg: tuple, judge: bool, gen_script: Path):
    vkey, seed, dream, form = cfg[:4]
    extra = cfg[4] if len(cfg) > 4 else {}
    if vkey not in VAULTS:
        print(f"   -- skipping {vkey} (not in vaults.local.json)")
        return
    vault = VAULTS[vkey]
    env = dict(os.environ, GEN_VAULT=vault, GEN_FORM=form, PYTHONIOENCODING="utf-8")
    if extra.get("spine"):
        env["GEN_SPINE"] = extra["spine"]
        env["GEN_GOAL"] = extra.get("goal", "")
    print(f"== generate {vkey} seed={seed} d={dream} form={form}"
          + (" [authored]" if extra.get("spine") else ""))
    subprocess.run([sys.executable, str(gen_script), f"{seed}:{dream}"],
                   env=env, cwd=str(gen_script.parent), check=False)
    out = gen_script.parent / _outname(vault, seed, dream, form,
                                       bool(extra.get("spine")))
    if out.exists():
        score(out, vault, judge, form)
        return HERE / "stories" / out.name
    print(f"   !! missing {out.name}")
    return None


def sweep_plan(n: int, sweep_seed: int = 7) -> list[dict]:
    import random
    rng = random.Random(sweep_seed)
    mix = [("story", d) for d in [0.85] * 6 + [0.5] * 4 + [0.2] * 2] + \
          [("tutorial", 0.5)] * 4 + [("case", 0.85), ("case", 0.5)] + \
          [("epistolary", 0.85), ("epistolary", 0.85)]
    vkeys = list(VAULTS)
    if not vkeys:
        sys.exit("no vaults configured — create evals/vaults.local.json")
    rng.shuffle(vkeys)
    return [{"vault": vkeys[i % len(vkeys)], "seed": rng.randint(100, 999),
             "dream": d, "form": f}
            for i, (f, d) in enumerate((mix * (n // len(mix) + 1))[:n])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--trend", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--pv", default="")
    ap.add_argument("--gen", default=str(GEN_DEFAULT))
    ap.add_argument("--sweep", type=int, default=0)
    ap.add_argument("--slice", default="")
    ap.add_argument("--sweep-seed", type=int, default=7)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--fingerprint", action="store_true",
                    help="post-pass: humanlikeness fingerprint on generated stories "
                         "(needs DWELL_FINGERPRINT_SCRIPT)")
    a = ap.parse_args()
    gen = Path(a.gen)
    if a.sweep:
        plan = sweep_plan(a.sweep, a.sweep_seed)
        if a.plan_only:
            for k, c in enumerate(plan):
                print(k, c)
            return
        lo, hi = 0, len(plan)
        if a.slice:
            lo, hi = (int(x) if x else d for x, d in
                      zip(a.slice.split(":"), (0, len(plan))))
        made = []
        for k in range(lo, min(hi, len(plan))):
            c = plan[k]
            made.append(run_cfg((c["vault"], c["seed"], c["dream"], c["form"]),
                                a.judge, gen))
        if a.fingerprint:
            _fingerprint_pass(made)
        trend()
        return
    if a.score_only:
        import glob as _g
        for pat in a.files:
            for f in sorted(_g.glob(pat)):
                score(Path(f), "", a.judge)
    elif a.run:
        made = [run_cfg(cfg, a.judge, gen) for cfg in BATTERY]
        if a.fingerprint:
            _fingerprint_pass(made)
    trend()


if __name__ == "__main__":
    main()
