"""review_server.py — dev-only reading + labeling UI for generated stories.

NOT part of the Dwell app. Serves the eval corpus (evals/stories/*.md) with
each story's judge verdicts beside it, and captures YOUR per-criterion labels
into labels.jsonl — which is the gold set the judges get calibrated against.

    python review_server.py            # http://127.0.0.1:8092
Queues: All · Unlabeled · Disagreement (Haiku vs Sonnet gap) · Labeled.
Stdlib only (http.server) — no deps, no build step.
"""
from __future__ import annotations
import json, re, statistics, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
STORIES = HERE / "stories"
HIST = HERE / "history.jsonl"
LABELS = HERE / "labels.jsonl"
HL = HERE / "humanlikeness.jsonl"          # the fingerprint point-graph data
POLES = HERE / "hl_poles.json"             # precomputed human / frontier-AI poles
PORT = 8092

CRITERIA = {
    "enacted": ["staged_overall", "ending_settled", "prot_changed", "connected",
                "premise_resolved", "mood_coherent", "named_on_page"],
    "didactic": ["promise_kept", "progression", "connected", "busywork_steps",
                 "stays_on_promise"],
    "expository": ["through_line", "connected", "lands"],
}
ENACTED = ("story", "case", "epistolary")
DIDACTIC = ("tutorial", "guided", "qa", "brief")


def fclass(form: str) -> str:
    return ("enacted" if form in ENACTED else
            "didactic" if form in DIDACTIC else "expository")


def load_hist() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if HIST.exists():
        for l in HIST.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
                out.setdefault(r["file"], []).append(r)
            except Exception:
                pass
    return out


def load_labels() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if LABELS.exists():
        for l in LABELS.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
                out[r["file"]] = r          # last write wins
            except Exception:
                pass
    return out


def guess_form(name: str, hist: list[dict]) -> str:
    if hist:
        return hist[-1].get("form", "story")
    for f in list(DIDACTIC) + ["case", "epistolary"]:
        if f in name:
            return f
    return "story"


def story_meta(name: str, hist_all, labels) -> dict:
    h = hist_all.get(name, [])
    latest = h[-1] if h else {}
    judged = [r for r in h if r.get("L3", {}).get("judge_score") is not None]
    models = {r["L3"].get("model", "?"): r["L3"]["judge_score"] for r in judged}
    scores = [r["L3"]["judge_score"] for r in judged]
    disagree = (max(scores) - min(scores)) >= 12 if len(scores) > 1 else False
    return {"file": name, "pv": latest.get("pv", "?"),
            "form": guess_form(name, h),
            "judge": judged[-1]["L3"]["judge_score"] if judged else None,
            "models": models, "disagree": disagree,
            "labeled": name in labels,
            "pages": latest.get("pages")}


CSS = """
:root{color-scheme:dark;--bg:#14151a;--panel:#1d1f27;--ink:#e8e4da;--dim:#8b8a83;
--acc:#d4a373;--good:#7fb069;--bad:#c94f4f;--mid:#d4c05a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 Georgia,serif}
a{color:var(--acc);text-decoration:none}
.wrap{max-width:1200px;margin:0 auto;padding:24px}
h1{font-size:20px;letter-spacing:.04em} .dim{color:var(--dim)}
table{width:100%;border-collapse:collapse;font-family:ui-monospace,Consolas,monospace;font-size:13px}
td,th{padding:6px 10px;text-align:left;border-bottom:1px solid #2a2c36}
tr:hover td{background:#22242e}
.tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;background:#2a2c36}
.tag.warn{background:#4a2f2f;color:#e8a0a0}.tag.ok{background:#2f4a33;color:#a0e8ac}
.reader{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:28px}
.prose{font-size:17px;line-height:1.75;max-width:66ch}
.prose h3{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--dim);
border-top:1px solid #2a2c36;padding-top:14px;margin-top:30px}
.side{position:sticky;top:12px;align-self:start;max-height:96vh;overflow-y:auto;
background:var(--panel);border-radius:10px;padding:16px;font-size:13px}
.side h4{margin:14px 0 6px;font-family:ui-monospace,Consolas,monospace;font-size:11px;
letter-spacing:.08em;color:var(--dim);text-transform:uppercase}
.crit{margin-bottom:10px}.crit .name{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.crit .ev{color:var(--dim);font-size:12px;font-style:italic;margin:2px 0 4px}
.btn{cursor:pointer;border:1px solid #3a3d4a;background:#22242e;color:var(--ink);
border-radius:6px;padding:2px 11px;margin-right:4px;font:12px ui-monospace,Consolas,monospace}
.btn.sel0{background:var(--bad);color:#fff;border-color:var(--bad)}
.btn.sel1{background:var(--mid);color:#222;border-color:var(--mid)}
.btn.sel2{background:var(--good);color:#122;border-color:var(--good)}
textarea{width:100%;background:#22242e;color:var(--ink);border:1px solid #3a3d4a;
border-radius:6px;min-height:64px;font:13px Georgia,serif;padding:8px}
.save{margin-top:10px;width:100%;padding:8px;font-size:14px}
.plot{background:var(--panel);border-radius:10px;padding:14px 18px;margin-bottom:22px;
font-size:14px}.plot li{margin:3px 0}
.jscore{font-size:22px;font-family:ui-monospace,Consolas,monospace}
"""

PAGE_RE = re.compile(r"^## page (\d+) · (\w+) · (.+?) · (\d+)w\s*$", re.M)


def render_story_html(name: str) -> str:
    text = (STORIES / name).read_text(encoding="utf-8")
    head, _, _ = text.partition("\n---\n")
    heads = list(PAGE_RE.finditer(text))
    pages = []
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        body = text[h.end():end].split("\npages=")[0]
        body = re.sub(r"\n---\s*$", "", body).strip()
        paras = "".join(f"<p>{p}</p>" for p in body.split("\n\n"))
        paras = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", paras)
        paras = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", paras)
        pages.append(f"<h3>page {h.group(1)} · {h.group(2)} · {h.group(3)}</h3>{paras}")
    plot = "".join(f"<li>{l}</li>" for l in head.splitlines()
                   if re.match(r"\d+\.", l.strip()))
    hdr = "".join(f"<div>{l}</div>" for l in head.splitlines()
                  if l.startswith(("PROTAGONIST:", "PREMISE:", "CAST:", "PALETTE:",
                                   "PV:", "FORM:", "spine:")))
    return (f"<div class=plot>{hdr}<ol>{plot}</ol></div>"
            f"<div class=prose>{''.join(pages)}</div>")


def side_panel(name: str, hist_all, labels) -> str:
    h = hist_all.get(name, [])
    judged = [r for r in h if r.get("L3", {}).get("judge_score") is not None]
    meta = story_meta(name, hist_all, labels)
    fc = fclass(meta["form"])
    out = [f"<div class=jscore>judge: {meta['judge'] if meta['judge'] is not None else '—'}"
           f" <span class=dim style='font-size:12px'>{meta['pv']} · {meta['form']}</span></div>"]
    if len(meta["models"]) > 1:
        out.append("<div class=dim>" + " · ".join(
            f"{m.split('-')[1]}={s}" for m, s in meta["models"].items()) + "</div>")
    if judged:
        st = judged[-1]["L3"].get("story") or {}
        ev = st.get("evidence") or {}
        out.append("<h4>judge verdicts</h4>")
        for k, v in st.items():
            if k == "evidence":
                continue
            q = ev.get(k, "") if isinstance(ev, dict) else ""
            out.append(f"<div class=crit><span class=name>{k} = <b>{v}</b></span>"
                       + (f"<div class=ev>{q}</div>" if q else "") + "</div>")
    lab = labels.get(name, {})
    out.append("<h4>your labels</h4>")
    for c in CRITERIA[fc] + ["overall"]:
        cur = (lab.get("scores") or {}).get(c)
        btns = "".join(
            f"<button class='btn{' sel' + str(v) if cur == v else ''}' "
            f"onclick=\"setLab('{c}',{v},this)\">{v}</button>" for v in (0, 1, 2))
        out.append(f"<div class=crit><span class=name>{c}</span><br>{btns}</div>")
    notes = (lab.get("notes") or "").replace("<", "&lt;")
    out.append(f"<h4>notes</h4><textarea id=notes>{notes}</textarea>")
    out.append("<button class='btn save' onclick='saveLab()'>save labels</button>"
               "<div id=savemsg class=dim></div>")
    out.append(f"""<script>
const FILE={json.dumps(name)}; const LAB={json.dumps(lab.get('scores') or {})};
function setLab(c,v,el){{LAB[c]=v;
 for(const b of el.parentElement.querySelectorAll('.btn'))b.className='btn';
 el.className='btn sel'+v;}}
function saveLab(){{fetch('/label',{{method:'POST',headers:{{'content-type':'application/json'}},
 body:JSON.stringify({{file:FILE,scores:LAB,notes:document.getElementById('notes').value}})}})
 .then(r=>r.json()).then(j=>{{document.getElementById('savemsg').textContent=
 'saved '+new Date().toLocaleTimeString();}});}}
</script>""")
    return "<div class=side>" + "".join(out) + "</div>"


def list_page(queue: str) -> str:
    hist_all, labels = load_hist(), load_labels()
    metas = [story_meta(p.name, hist_all, labels)
             for p in sorted(STORIES.glob("*.md"))]
    if queue == "unlabeled":
        metas = [m for m in metas if not m["labeled"]]
    elif queue == "disagree":
        metas = [m for m in metas if m["disagree"]]
    elif queue == "labeled":
        metas = [m for m in metas if m["labeled"]]
    metas.sort(key=lambda m: (m["judge"] is None, m["judge"] or 0))
    tabs = " · ".join(
        f"<a href='/?q={q}'>{'<b>' + t + '</b>' if q == queue else t}</a>"
        for q, t in (("all", "All"), ("unlabeled", "Unlabeled"),
                     ("disagree", "Disagreement"), ("labeled", "Labeled")))
    rows = "".join(
        f"<tr><td><a href='/story/{m['file']}'>{m['file']}</a></td>"
        f"<td>{m['pv']}</td><td>{m['form']}</td>"
        f"<td>{m['judge'] if m['judge'] is not None else '—'}</td>"
        f"<td>{'<span class=\"tag warn\">split</span>' if m['disagree'] else ''}</td>"
        f"<td>{'<span class=\"tag ok\">labeled</span>' if m['labeled'] else ''}</td></tr>"
        for m in metas)
    n_lab = sum(1 for m in metas if m["labeled"])
    return (f"<!doctype html><meta charset=utf-8><title>Dwell evals</title>"
            f"<style>{CSS}</style><div class=wrap><h1>Dwell eval reader "
            f"<span class=dim>· gold-set labeling · {len(metas)} shown · "
            f"{n_lab} labeled · <a href='/humanlikeness'>humanlikeness graph &rarr;</a>"
            f"</span></h1><p>{tabs}</p>"
            f"<table><tr><th>story</th><th>pv</th><th>form</th><th>judge</th>"
            f"<th></th><th></th></tr>{rows}</table></div>")


# ------------------------------------------------------------------ humanlikeness
# The live point graph (dev dashboard, NOT the product UI). Reads
# humanlikeness.jsonl (one row per fingerprinted story) + hl_poles.json (the
# precomputed HUMAN and FRONTIER-AI reference poles). One pinned rater
# (claude-haiku-4-5-20251001) makes points comparable; mid-range story-level
# scores are rater-sensitive — the DISTRIBUTION is the signal, never a story grade.

def load_hl() -> list[dict]:
    rows = []
    if HL.exists():
        for l in HL.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
                if "P_human" in r:
                    rows.append(r)
            except Exception:
                pass
    return rows


def _pv_key(pv: str):
    # order engine versions: numeric core then any suffix (p19 < p25a < p25b)
    m = re.match(r"p?(\d+)([a-z]*)", str(pv))
    return (int(m.group(1)), m.group(2)) if m else (0, str(pv))


def _mm(vals):
    if not vals:
        return None, None
    return statistics.mean(vals), statistics.median(vals)


def _beeswarm(xs, x0, x1, cy, r, half):
    """Greedy column-bucket beeswarm: densest at the mode, clamped to the band.
    Returns pixel (px, py) in the input order."""
    colw = max(2 * r, 1.0)
    cols: dict = {}
    res = [None] * len(xs)
    for i in sorted(range(len(xs)), key=lambda k: xs[k]):
        px = x0 + max(0.0, min(1.0, xs[i])) * (x1 - x0)
        col = round(px / colw)
        c = cols.get(col, 0)
        cols[col] = c + 1
        step = ((c + 1) // 2) * (2 * r * 0.92)
        py = cy + (0 if c == 0 else (step if c % 2 else -step))
        py = max(cy - half + r, min(cy + half - r, py))
        res[i] = (px, py)
    return res


_PV_RAMP = ["#4a5568", "#5b6b8c", "#6b8cae", "#5aa0a0", "#8aa04a", "#c0913f"]


def humanlikeness_page() -> str:
    rows = load_hl()
    poles = {}
    if POLES.exists():
        try:
            poles = json.loads(POLES.read_text(encoding="utf-8"))
        except Exception:
            poles = {}

    # ---- per-PV stats + which PV is "current" (newest) --------------------
    by_pv: dict = {}
    for r in rows:
        by_pv.setdefault(r.get("pv", "?"), []).append(r["P_human"])
    pvs = sorted(by_pv, key=_pv_key)
    cur_pv = pvs[-1] if pvs else None
    prev_pv = pvs[-2] if len(pvs) > 1 else None
    all_p = [r["P_human"] for r in rows]
    o_mean, o_med = _mm(all_p)
    c_mean, c_med = _mm(by_pv.get(cur_pv, []))
    p_mean, _ = _mm(by_pv.get(prev_pv, [])) if prev_pv else (None, None)
    trend = (c_mean - p_mean) if (c_mean is not None and p_mean is not None) else None

    def fmt(v, d=3):
        return f"{v:.{d}f}" if v is not None else "—"

    trend_txt = ""
    if trend is not None:
        arrow = "▲" if trend > 0 else ("▼" if trend < 0 else "—")
        cls = "good" if trend > 0 else ("bad" if trend < 0 else "dim")
        trend_txt = (f"<span class={cls}>{arrow} {trend:+.3f}</span> "
                     f"<span class=dim>vs {prev_pv}</span>")

    # ---- geometry --------------------------------------------------------
    W, x0, x1 = 1120, 150, 1080
    lanes = [("HUMAN", 70, 60, poles.get("human", []), "#7fb069", 3, None),
             (f"DWELL", 250, 150, None, None, 5, None),   # filled below
             ("FRONTIER-AI", 470, 60, (poles.get("ai", []) + poles.get("controls", [])),
              "#c94f4f", 3, None)]
    Ht = 560
    svg = [f"<svg viewBox='0 0 {W} {Ht}' width='100%' style='min-width:760px'>"]
    # x grid + ticks
    for t in (0, .25, .5, .75, 1.0):
        gx = x0 + t * (x1 - x0)
        svg.append(f"<line x1={gx:.0f} y1=40 x2={gx:.0f} y2=530 stroke='#2a2c36'/>")
        svg.append(f"<text x={gx:.0f} y=548 fill='#8b8a83' font-size=12 "
                   f"text-anchor=middle>{t:.2f}</text>")
    svg.append("<text x=615 y=24 fill='#8b8a83' font-size=13 text-anchor=middle>"
               "P(human) &mdash; joint-encoded, Haiku-rated</text>")

    # human + frontier lanes (poles)
    for label, cy, half, vals, color, rr, _ in lanes:
        if vals is None:
            continue
        svg.append(f"<text x=20 y={cy+4} fill='#8b8a83' font-size=13>{label}</text>")
        if vals:
            m = statistics.mean(vals)
            svg.append(f"<text x=20 y={cy+20} fill='#5c5b55' font-size=11>"
                       f"n={len(vals)} &micro;={m:.2f}</text>")
            for (px, py) in _beeswarm(vals, x0, x1, cy, rr, half):
                svg.append(f"<circle cx={px:.1f} cy={py:.1f} r={rr} "
                           f"fill='{color}' fill-opacity=0.55/>")

    # DWELL lane — colored by PV era, newest highlighted
    d_cy, d_half = 250, 150
    svg.append(f"<text x=20 y={d_cy+4} fill='#e8e4da' font-size=13>DWELL</text>")
    if all_p:
        svg.append(f"<text x=20 y={d_cy+20} fill='#5c5b55' font-size=11>"
                   f"n={len(all_p)} &micro;={o_mean:.2f}</text>")
        pv_color = {pv: _PV_RAMP[min(i, len(_PV_RAMP) - 1)]
                    for i, pv in enumerate(pvs)}
        xs = [r["P_human"] for r in rows]
        pos = _beeswarm(xs, x0, x1, d_cy, 5, d_half)
        for r, (px, py) in zip(rows, pos):
            newest = r.get("pv") == cur_pv
            col = "#e8b04a" if newest else pv_color.get(r.get("pv"), "#6b7280")
            rr = 6 if newest else 4.5
            stroke = (";stroke:#e8e4da;stroke-width:1.2" if r.get("staged")
                      else "")
            svg.append(f"<circle cx={px:.1f} cy={py:.1f} r={rr} fill='{col}' "
                       f"fill-opacity={0.95 if newest else 0.7} style='{stroke}'>"
                       f"<title>{r.get('file','?')}  P={r['P_human']:.3f}  "
                       f"{r.get('pv','?')}{'  staged' if r.get('staged') else ''}</title>"
                       f"</circle>")
        # mean / median reference lines for DWELL
        for val, lab, dash in ((o_mean, "mean", "4 3"), (o_med, "median", "1 3")):
            lx = x0 + val * (x1 - x0)
            svg.append(f"<line x1={lx:.0f} y1={d_cy-d_half} x2={lx:.0f} "
                       f"y2={d_cy+d_half} stroke='#d4a373' stroke-dasharray='{dash}' "
                       f"stroke-opacity=0.7/>")
            svg.append(f"<text x={lx:.0f} y={d_cy-d_half-4} fill='#d4a373' "
                       f"font-size=10 text-anchor=middle>{lab} {val:.2f}</text>")
    else:
        svg.append(f"<text x=615 y={d_cy} fill='#8b8a83' font-size=14 "
                   f"text-anchor=middle>no fingerprints yet — run "
                   f"battery.py --fingerprint or fresh_corpus + fingerprint.py</text>")
    svg.append("</svg>")

    # ---- PV legend -------------------------------------------------------
    legend = " ".join(
        f"<span class=chip style='background:{('#e8b04a' if pv==cur_pv else _PV_RAMP[min(i,len(_PV_RAMP)-1)])};"
        f"color:#181818'>{pv} · n={len(by_pv[pv])} · &micro;{statistics.mean(by_pv[pv]):.2f}</span>"
        for i, pv in enumerate(pvs))

    hdr = (f"<div class=tally>"
           f"<div><span class=big>{len(rows)}</span><span class=lbl>stories fingerprinted</span></div>"
           f"<div><span class=big>{fmt(o_mean)}</span><span class=lbl>mean P(human) · median {fmt(o_med)}</span></div>"
           f"<div><span class=big>{fmt(c_mean)}</span><span class=lbl>{cur_pv or '—'} mean · median {fmt(c_med)} · n={len(by_pv.get(cur_pv,[]))}</span></div>"
           f"<div><span class=big>{trend_txt or '—'}</span><span class=lbl>current-PV trend</span></div>"
           f"</div>")

    css2 = """
    .tally{display:flex;gap:34px;flex-wrap:wrap;background:var(--panel);border-radius:10px;
    padding:16px 22px;margin:14px 0 6px;font-family:ui-monospace,Consolas,monospace}
    .tally .big{font-size:26px;display:block}.tally .lbl{font-size:11px;color:var(--dim)}
    .good{color:var(--good)}.bad{color:var(--bad)}
    .chip{display:inline-block;padding:2px 9px;border-radius:9px;font-size:11px;margin:2px;
    font-family:ui-monospace,Consolas,monospace}
    .plotwrap{background:var(--panel);border-radius:10px;padding:10px 6px;overflow-x:auto}
    .note{color:var(--dim);font-size:12px;font-style:italic;margin:10px 2px}
    """
    poll = f"""<script>
    let _m=0;
    async function tick(){{try{{const r=await fetch('/hl_mtime');const j=await r.json();
      if(_m&&j.mtime!==_m){{location.reload();}} _m=j.mtime;}}catch(e){{}}}}
    setInterval(tick,4000);tick();
    </script>"""
    return (f"<!doctype html><meta charset=utf-8><title>Dwell humanlikeness</title>"
            f"<style>{CSS}{css2}</style><div class=wrap>"
            f"<h1>Dwell humanlikeness <span class=dim>· live fingerprint point graph "
            f"· <a href='/'>&larr; eval reader</a></span></h1>"
            f"{hdr}<div class=legend style='margin:2px 0 10px'>{legend}</div>"
            f"<div class=plotwrap>{''.join(svg)}</div>"
            f"<p class=note>Poles: HUMAN = {poles.get('n_human','?')} of their released "
            f"human-class vectors (&micro;{fmt(poles.get('human_mean'),2)}); FRONTIER-AI = "
            f"their AI vectors + our {poles.get('n_controls','?')} control extractions "
            f"(&micro;{fmt(poles.get('ai_mean'),2)}) — both scored through the same "
            f"joint-encode path. Newest PV highlighted gold; staged runs ringed. "
            f"One pinned rater (claude-haiku-4-5-20251001). "
            f"<b>Mid-range story-level scores are rater-sensitive — read the "
            f"distribution, never a single story's grade.</b></p>"
            f"</div>{poll}")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body: str, ctype="text/html; charset=utf-8", code=200):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/hl_mtime":
            mt = HL.stat().st_mtime if HL.exists() else 0
            return self._send(json.dumps({"mtime": mt}), "application/json")
        if self.path.startswith("/humanlikeness"):
            return self._send(humanlikeness_page())
        if self.path.startswith("/story/"):
            name = unquote(self.path[len("/story/"):])
            if not (STORIES / name).exists() or "/" in name or "\\" in name:
                return self._send("not found", code=404)
            hist_all, labels = load_hist(), load_labels()
            body = (f"<!doctype html><meta charset=utf-8><title>{name}</title>"
                    f"<style>{CSS}</style><div class=wrap>"
                    f"<p><a href='/'>&larr; back</a> <span class=dim>{name}</span></p>"
                    f"<div class=reader><div>{render_story_html(name)}</div>"
                    f"{side_panel(name, hist_all, labels)}</div></div>")
            return self._send(body)
        q = "all"
        if "?q=" in self.path:
            q = self.path.split("?q=")[1].split("&")[0]
        return self._send(list_page(q))

    def do_POST(self):
        if self.path != "/label":
            return self._send("{}", "application/json", 404)
        n = int(self.headers.get("content-length", 0))
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            row = {"file": data["file"], "scores": data.get("scores") or {},
                   "notes": data.get("notes", ""),
                   "ts": time.strftime("%Y-%m-%d %H:%M"), "labeler": "human"}
            with open(LABELS, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return self._send('{"ok":true}', "application/json")
        except Exception as e:
            return self._send(json.dumps({"ok": False, "err": str(e)[:80]}),
                              "application/json", 400)


if __name__ == "__main__":
    print(f"Dwell eval reader on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
