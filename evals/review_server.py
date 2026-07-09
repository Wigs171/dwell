"""review_server.py — dev-only reading + labeling UI for generated stories.

NOT part of the Dwell app. Serves the eval corpus (evals/stories/*.md) with
each story's judge verdicts beside it, and captures YOUR per-criterion labels
into labels.jsonl — which is the gold set the judges get calibrated against.

    python review_server.py            # http://127.0.0.1:8092
Queues: All · Unlabeled · Disagreement (Haiku vs Sonnet gap) · Labeled.
Stdlib only (http.server) — no deps, no build step.
"""
from __future__ import annotations
import json, re, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
STORIES = HERE / "stories"
HIST = HERE / "history.jsonl"
LABELS = HERE / "labels.jsonl"
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
            f"{n_lab} labeled</span></h1><p>{tabs}</p>"
            f"<table><tr><th>story</th><th>pv</th><th>form</th><th>judge</th>"
            f"<th></th><th></th></tr>{rows}</table></div>")


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
