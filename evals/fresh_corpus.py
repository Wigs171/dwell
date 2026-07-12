"""fresh_corpus.py — generate a fresh eval corpus with a staged same-seed arm.

Two arms on production defaults:
  DEFAULT: N enacted stories across your vaults, mixed dream bands, fresh seeds.
  STAGED : the first K configs re-rendered with DWELL_STAGED=1 under the SAME
           (vault, seed, dream) — same-seed staged-vs-single pairs, a clean
           directional A/B on the staged pipeline.

Vaults are machine-local (vaults.local.json — same convention as battery.py).
Generates via gen_path.py, archives into evals/stories/. Idempotent: existing
outputs are skipped, so a killed run resumes.

    python fresh_corpus.py --plan-only
    python fresh_corpus.py --arm both
    python fresh_corpus.py --post          # judge (r7) + fingerprint existing outputs

The post pass needs ANTHROPIC_API_KEY (judge) and, for the humanlikeness
fingerprint, DWELL_FINGERPRINT_SCRIPT pointing at your StoryScope bridge's
fingerprint.py (see storyscope_bridge/README.md) — unset skips it.
"""
from __future__ import annotations
import argparse, json, os, random, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE / "gen_path.py"
STORIES = HERE / "stories"
FP_SCRIPT = Path(os.environ.get("DWELL_FINGERPRINT_SCRIPT", ""))


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


def build_plan(seed: int = 71, n_default: int = 24,
               n_staged: int = 6) -> tuple[list[dict], list[dict]]:
    if not VAULTS:
        sys.exit("no vaults configured — create evals/vaults.local.json")
    rng = random.Random(seed)
    vkeys = list(VAULTS)
    used = set()

    def fresh_seed():
        while True:
            s = rng.randint(100, 999)
            if s not in used:
                used.add(s)
                return s

    n_staged = min(n_staged, n_default)
    paired_dreams = ([0.85] * 4 + [0.5] * 2) * (n_staged // 6 + 1)
    paired = [{"vault": vkeys[i % len(vkeys)], "seed": fresh_seed(),
               "dream": paired_dreams[i], "form": "story"}
              for i in range(n_staged)]
    n_extra = n_default - n_staged
    extra_forms = (["story"] * max(0, n_extra - 4)
                   + ["case", "case", "epistolary", "epistolary"])[:n_extra]
    extra_dreams = ([0.85] * (n_extra // 2) + [0.5] * (n_extra // 3)
                    + [0.2] * n_extra)[:n_extra]
    rng.shuffle(extra_forms)
    rng.shuffle(extra_dreams)
    extra = [{"vault": vkeys[i % len(vkeys)], "seed": fresh_seed(),
              "dream": extra_dreams[i], "form": extra_forms[i]}
             for i in range(n_extra)]
    default = paired + extra
    staged = [dict(c, staged=True) for c in paired]
    for c in default:
        c["staged"] = False
    return default, staged


def out_name(c: dict) -> str:
    vtag = Path(VAULTS[c["vault"]]).name.split()[0].lower()
    dtag = str(c["dream"]).replace(".", "")
    ftag = "" if c["form"] == "story" else f"_{c['form']}"
    stag = "_stg" if c.get("staged") else ""
    return f"path_test_{vtag}gen_{c['seed']}_d{dtag}{ftag}{stag}.md"


def gen_one(c: dict) -> Path | None:
    out = HERE / out_name(c)
    dst = STORIES / out.name
    if dst.exists():
        print(f"  = exists, skip: {out.name}", flush=True)
        return dst
    env = dict(os.environ, GEN_VAULT=VAULTS[c["vault"]], GEN_FORM=c["form"],
               PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8",
               DWELL_STAGED="1" if c.get("staged") else "")
    print(f"== gen {c['vault']} seed={c['seed']} d={c['dream']} "
          f"form={c['form']}{' STAGED' if c.get('staged') else ''}", flush=True)
    subprocess.run([sys.executable, str(GEN), f"{c['seed']}:{c['dream']}"],
                   env=env, cwd=str(GEN.parent), check=False)
    if not out.exists():
        print(f"  !! missing {out.name}", flush=True)
        return None
    STORIES.mkdir(exist_ok=True)
    shutil.copy2(out, dst)
    return dst


def post_pass(seed: int, n_default: int, n_staged: int,
              judge: bool, fingerprint: bool, pv: str) -> None:
    default, staged = build_plan(seed, n_default, n_staged)
    made = [(STORIES / out_name(c), c) for c in (default + staged)
            if (STORIES / out_name(c)).exists()]
    print(f"post-pass over {len(made)} generated stories", flush=True)
    if judge:
        for f, c in made:
            print(f"== judge r7 {f.name}", flush=True)
            cmd = [sys.executable, str(HERE / "score_story.py"), str(f),
                   "--vault", VAULTS[c["vault"]], "--judge", "--rubric", "r7",
                   "--form", c["form"]]
            if pv:
                cmd += ["--pv", pv]
            subprocess.run(cmd, check=False,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    if fingerprint and made:
        if not os.environ.get("DWELL_FINGERPRINT_SCRIPT") or not FP_SCRIPT.exists():
            print("[fingerprint skipped: set DWELL_FINGERPRINT_SCRIPT]")
            return
        print(f"== fingerprint {len(made)} stories", flush=True)
        subprocess.run([sys.executable, str(FP_SCRIPT), *[str(f) for f, _ in made]],
                       check=False, env=dict(os.environ, PYTHONIOENCODING="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["default", "staged", "both"], default="both")
    ap.add_argument("--slice", default="", help="lo:hi within the chosen arm")
    ap.add_argument("--seed", type=int, default=71)
    ap.add_argument("--n", type=int, default=24, help="default-arm size")
    ap.add_argument("--n-staged", type=int, default=6, help="staged same-seed pairs")
    ap.add_argument("--pv", default="", help="prompt-version tag for the judge rows")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--post", action="store_true",
                    help="skip generation; judge (r7) + fingerprint existing outputs")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--no-fingerprint", action="store_true")
    a = ap.parse_args()
    if a.post:
        post_pass(a.seed, a.n, a.n_staged, not a.no_judge, not a.no_fingerprint, a.pv)
        return
    default, staged = build_plan(a.seed, a.n, a.n_staged)
    if a.plan_only:
        print(f"# DEFAULT ARM ({len(default)})")
        for i, c in enumerate(default):
            print(f"  {i:>2} {out_name(c)}")
        print(f"# STAGED ARM ({len(staged)}, same seeds as default[:{len(staged)}])")
        for i, c in enumerate(staged):
            print(f"  {i:>2} {out_name(c)}")
        return
    arms = {"default": default, "staged": staged, "both": default + staged}[a.arm]
    lo, hi = 0, len(arms)
    if a.slice:
        parts = a.slice.split(":")
        lo = int(parts[0]) if parts[0] else 0
        hi = int(parts[1]) if len(parts) > 1 and parts[1] else len(arms)
    made = [p.name for k in range(lo, min(hi, len(arms)))
            if (p := gen_one(arms[k]))]
    print(f"\nGEN DONE — {len(made)} files in {STORIES}")


if __name__ == "__main__":
    main()
