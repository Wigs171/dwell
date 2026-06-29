"""
dwell_smoke.py — end-to-end smoke test for the Dwell FastAPI server.

Exercises every endpoint against a real vault, parsing the SSE streams. Run the
server first (python server/dwell_server.py), then:

    python tests/dwell_smoke.py                 # dry pass only (no API cost)
    python tests/dwell_smoke.py --live          # + a live Mercury pass
    python tests/dwell_smoke.py --vault "<path>" --base http://127.0.0.1:8000
    # set DWELL_SMOKE_VAULT or pass --vault to choose the vault to exercise

Uses only the stdlib (urllib) so it has no dependencies of its own.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
# Point the smoke test at a vault the running server can see (under its VAULT_ROOT).
DEFAULT_VAULT = os.environ.get("DWELL_SMOKE_VAULT", "")


def _req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode())


def get(path):
    return _req("GET", path)


def post(path, body):
    return _req("POST", path, body)


def stream(path: str, body: dict):
    """POST an SSE endpoint; yield (event, payload) as they arrive."""
    data = json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method="POST",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=180) as resp:
        ev, buf = "message", []
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")   # sse_starlette uses CRLF
            if line == "":                          # event terminator
                if buf:
                    try:
                        yield ev, json.loads("\n".join(buf))
                    except Exception:
                        pass
                ev, buf = "message", []
            elif line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                buf.append(line[5:].lstrip(" "))


def read_page(sid, action="auto", plan_id=None, start="new", diffusing=True):
    """Consume one /page stream; return (done_payload, frame_count)."""
    frames, done, started = 0, None, None
    for ev, p in stream("/page", {"session": sid, "action": action,
                                  "plan_id": plan_id, "start": start,
                                  "diffusing": diffusing}):
        if ev == "start":
            started = p
        elif ev == "frame":
            frames += 1
        elif ev == "done":
            done = p
        elif ev == "error":
            raise RuntimeError(p.get("message"))
    assert started is not None, "no start event"
    assert done is not None, "no done event"
    return done, frames


def check(label, cond, extra=""):
    mark = "OK  " if cond else "FAIL"
    print(f"  [{mark}] {label}{('  ' + extra) if extra else ''}")
    if not cond:
        check.failed += 1
check.failed = 0


def run_pass(vault, engine, dry, label):
    print(f"\n=== {label} (engine={engine or 'default'}, dry={dry}) ===")
    info = post("/session", {"vault": vault, "engine": engine, "dry": dry})
    sid = info["session_id"]
    check("session created", bool(sid))
    check("nodes loaded", info["nodes"] > 0, f'{info["nodes"]} nodes · {info["embed_label"]}')
    check("menu offered", len(info["menu"]) >= 2)
    print(f"        provider={info['provider']} model={info['model']} "
          f"mode={info['mode']} voice={info['voices']['current']} "
          f"init_error={info['init_error']}")

    done, frames = read_page(sid, action="first", start="new")
    check("first page produced", len(done["text"]) > 200, f'{len(done["text"])} chars')
    check("first page has branches", len(done["branches"]) >= 1,
          f'{len(done["branches"])} branches')
    check("opening mode is 'open'", done["mode"] == "open", done["mode"])
    if not dry:
        check("live render streamed frames", frames >= 1, f"{frames} frames")
    first_tail = done["text"][-60:].replace("\n", " ")

    done2, _ = read_page(sid, action="auto")
    check("flow page produced", len(done2["text"]) > 200,
          f'mode={done2["mode"]}, marker={done2["marker"]}')

    branches = get(f"/branches?session={sid}")["branches"]
    check("/branches recomputes", len(branches) >= 1, f'{len(branches)} options')
    if branches:
        pid = branches[0]["plan_id"]
        done3, _ = read_page(sid, action="plan", plan_id=pid)
        check("branch (plan) page produced", len(done3["text"]) > 100,
              f'-> {done3["node"]}')

    post("/steer", {"session": sid, "text": "toward music and harmony"})
    done4, _ = read_page(sid, action="auto")
    check("steered page produced", len(done4["text"]) > 100,
          f'bucket={done4["steer_bucket"]}, -> {done4["node"]}')

    v = get(f"/voices?session={sid}")
    check("/voices lists presets", len(v["presets"]) >= 3)
    sv = post("/voice", {"session": sid, "name": "surfer"})
    check("/voice switches", sv["voice"] == "surfer", sv["voice_id"])

    cost = get(f"/state?session={sid}")["cost"]
    print(f"        session cost so far: ${cost}")

    if not dry:
        # expand the middle of the last page in place
        txt = done4["text"]
        mid = len(txt) // 2
        sel = txt[mid:mid + 80]
        acc = None
        for ev, p in stream("/expand", {"session": sid, "selected": sel,
                                        "before": txt[:mid], "after": txt[mid + 80:],
                                        "mode": "expand"}):
            if ev == "done":
                acc = p["text"]
            elif ev == "error":
                raise RuntimeError(p["message"])
        check("/expand returns reworked text", acc is not None and len(acc) > 0,
              f'{len(acc or "")} chars')

        missed = get(f"/missed?session={sid}&n=8")
        check("/missed returns pairs", isinstance(missed["pairs"], list),
              f'{len(missed["pairs"])} pairs ({missed["embed_label"]})')
        if missed["pairs"]:
            top = missed["pairs"][0]
            print(f'        top missed: {top["sim"]}  {top["title_a"]} ⇿ {top["title_b"]}')

    post("/session?session=" + sid, None) if False else None
    return sid


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--live", action="store_true", help="also run a live Mercury pass")
    ap.add_argument("--engine", default="mercury")
    args = ap.parse_args()
    BASE = args.base

    print(f"server: {get('/health')}")
    run_pass(args.vault, engine=None, dry=True, label="DRY PASS")
    if args.live:
        run_pass(args.vault, engine=args.engine, dry=False, label="LIVE PASS")

    print(f"\n{'='*40}")
    if check.failed:
        print(f"  {check.failed} CHECK(S) FAILED")
        sys.exit(1)
    print("  ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
