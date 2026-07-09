"""gen_path.py — generate a scoreable path file (the eval harness's generator).

    GEN_VAULT=<path to a vault> [GEN_FORM=story|tutorial|...] \
    [GEN_SPINE="node-id,node-id,..."] [GEN_GOAL="..."] \
    python gen_path.py <seed>:<dream> [...]

Walks a path (random spine, or an AUTHORED one via GEN_SPINE — the product
shape for tutorials: an expert picks the nodes and states the training goal)
and writes `path_test_<vault>gen_<seed>_d<dream>[_form][_auth].md` beside this
script, in the format score_story.py parses. Requires the render engine's API
key configured as usual (.env).
"""
import io, os, random, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTO = HERE.parent / "server"
if not PROTO.exists():
    PROTO = HERE.parent / "prototypes"
sys.path.insert(0, str(PROTO))
import dwell
from dwell import Brain, PathNavigator, Renderer, plot_kind_for
from dwell_paths import generate_spine
from compendium.vault.layout import VaultPaths

OUT = HERE
VAULT = os.environ.get("GEN_VAULT", "")
FORM = os.environ.get("GEN_FORM", "story")
SPINE = [s.strip() for s in os.environ.get("GEN_SPINE", "").split(",") if s.strip()]
GOAL = os.environ.get("GEN_GOAL", "")
MAXP = 20
if not VAULT:
    sys.exit("set GEN_VAULT to a vault directory")
VTAG = Path(VAULT).name.split()[0].lower()


def tail_of(t, n=300):
    t = " ".join(t.split())
    if len(t) <= n:
        return t
    cut = t[-n:]
    d = cut.find(". ")
    return cut[d + 2:] if 0 <= d < n - 80 else cut


def mkc(r):
    def _c(s, u):
        t, i, o = r._complete(s, u, diffusing=False)
        try:
            r.cost_tracker.record_call(input_tokens=i, output_tokens=o,
                                       model=r.model, is_sub_call=True)
        except Exception:
            pass
        return t
    return _c


def run(brain, seed, dream=0.5):
    if SPINE:
        missing = [s for s in SPINE if s not in brain.nodes]
        if missing:
            raise SystemExit(f"GEN_SPINE nodes not in vault: {missing}")
        spine = SPINE
    else:
        spine = generate_spine(brain, random.Random(seed), length=5, temperature=0.6)
    nav = PathNavigator(brain, spine, random.Random(seed), goal=GOAL, tween_density=2)
    r = Renderer(brain.topic or VTAG, dry=False, voice=brain.voice_default or "clean",
                 vault_voices=brain.voice_profiles)
    r.set_form(FORM)
    r.set_dream(dream)
    nav.ensure_plot(mkc(r), kind=plot_kind_for(FORM), dream=dream)
    kinds = {g: brain.nodes[g].kind for g in spine}
    L = [f"# pathgen seed={seed} — {FORM} dream={dream}",
         f"PV: {dwell._PROMPT_V}", f"FORM: {FORM}",
         f"PROTAGONIST: {nav.protagonist or '(none — factual tour)'}",
         f"CAST: {nav.plot_cast}",
         "PALETTE: " + "; ".join(f"{n} — {g}" if g else n for n, g in nav.mood_palette),
         "spine: " + " -> ".join(f"{brain.nodes[g].title}[{kinds[g][:4]}]" for g in spine),
         "", "## THE PLOT", f"PREMISE: {nav.plot_premise}"]
    _w = nav.plot_weights or [1] * len(nav.plot_events)
    _m = nav.plot_moods or [""] * len(nav.plot_events)
    L += [f"{i+1}. [{_w[i]}p] {e}"
          + (f"  (mood: {_m[i]})" if i < len(_m) and _m[i] else "")
          + (f"  (price: {c})" if c else "")
          for i, (e, c) in enumerate(zip(nav.plot_events,
                                         nav.plot_costs or [''] * len(nav.plot_events)))]
    L.append("")
    print(f"[seed {seed}] prot={nav.protagonist} :: {nav.plot_premise[:90]}", flush=True)
    tail = ""
    plan = nav.plan_first()
    n = 0
    while plan is not None and n < MAXP:
        try:
            text = r.render(plan, tail, nav.recap(), "")
        except Exception as e:
            text = f"[FAIL {str(e)[:150]}]"
        nav.commit(plan)
        if hasattr(nav, 'observe_canon'):
            nav.observe_canon(text)
        nav.add_digest(r.digest_line(text))
        n += 1
        wp = f" · WAYPOINT={brain.nodes[plan.waypoint].title}" if plan.waypoint else ""
        L.append(f"\n---\n## page {n} · {plan.mode} · arc={plan.arc} · "
                 f"node={plan.node}{wp} · {len(text.split())}w\n")
        L.append(text)
        tail = tail_of(text)
        plan = nav.plan_auto()
    cost = r.cost_tracker.get_summary().get("estimated_cost_usd", 0)
    L.append(f"\n---\npages={n} cost=${cost:.4f} complete={nav.complete}")
    dtag = str(dream).replace(".", "")
    ftag = "" if FORM == "story" else f"_{FORM}"
    atag = "_auth" if SPINE else ""
    out = OUT / f"path_test_{VTAG}gen_{seed}_d{dtag}{ftag}{atag}.md"
    io.open(out, "w", encoding="utf-8").write("\n".join(L))
    print(f"[seed {seed} dream {dream}] {n} pages ${cost:.4f} -> {out.name}", flush=True)


def main():
    brain = Brain.load(VaultPaths(Path(VAULT)), progress=lambda m: None)
    for a in sys.argv[1:]:
        if ":" in a:
            s, d = a.split(":")
            run(brain, int(s), float(d))
        else:
            for d in (0.2, 0.5, 0.8):
                run(brain, int(a), d)


if __name__ == "__main__":
    main()
