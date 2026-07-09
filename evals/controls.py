"""controls.py — literary controls for the judge (dev tooling).

Builds control files in the test-story format from PUBLIC-DOMAIN short stories:
  positive: the real story, true plan header  -> rubric must score it HIGH
  negative: same pages, order shuffled        -> connected must TANK
A rubric/judge version is only trusted if it passes both. Run:
    python controls.py            # build 4 files into stories/
    python controls.py --judge    # ...and judge them (logged, pv=control/-shuf)
"""
from __future__ import annotations
import json, random, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "stories"


def _clean(raw: str) -> str:
    return raw.replace("\r\n", "\n")


def extract(path: Path, start_pat: str, end_pat: str) -> str:
    t = _clean(path.read_text(encoding="utf-8", errors="replace"))
    # trim to the book body FIRST — the license boilerplate also names titles,
    # and matching there fed the judge legal text (it scored it 0, correctly)
    m = re.search(r"\*\*\* ?START OF (?:THE|THIS) PROJECT[^\n]*", t)
    if m:
        t = t[m.end():]
    m = re.search(r"\*\*\* ?END OF (?:THE|THIS) PROJECT", t)
    if m:
        t = t[:m.start()]
    starts = [m.start() for m in re.finditer(start_pat, t)]
    s = starts[-1]                     # last occurrence skips the TOC
    m = re.search(end_pat, t[s + 20:])
    e = s + 20 + (m.start() if m else len(t))
    body = t[s:e]
    body = re.sub(r"^.*\n", "", body, count=1)      # drop the title line
    return body.strip()


def paginate(text: str, target_words: int = 450) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pages, cur, n = [], [], 0
    for p in paras:
        p1 = " ".join(p.split())
        cur.append(p1)
        n += len(p1.split())
        if n >= target_words:
            pages.append("\n\n".join(cur))
            cur, n = [], 0
    if cur:
        if pages and n < target_words // 3:
            pages[-1] += "\n\n" + "\n\n".join(cur)
        else:
            pages.append("\n\n".join(cur))
    return pages


def build(name: str, title: str, header: dict, beats: list[dict],
          pages: list[str], shuffle: bool = False) -> Path:
    if shuffle:
        pages = pages[:]
        random.Random(13).shuffle(pages)
    B, N = len(beats), len(pages)
    L = [f"# control — {title}" + (" (SHUFFLED)" if shuffle else ""),
         f"PV: {'control-shuf' if shuffle else 'control'}", "FORM: story",
         f"PROTAGONIST: {header['prot']}", f"CAST: {header['cast']}",
         f"PALETTE: {header['palette']}",
         f"spine: {header['spine']}", "", "## THE PLOT",
         f"PREMISE: {header['premise']}"]
    for i, b in enumerate(beats):
        L.append(f"{i + 1}. [1p] {b['event']}"
                 + (f"  (mood: {b['mood']})" if b.get("mood") else "")
                 + (f"  (price: {b['price']})" if b.get("price") else ""))
    L.append("")
    prev_beat = 0
    for k, body in enumerate(pages):
        beat = min(B, max(1, round((k + 1) * B / N)))
        mode = "move" if beat != prev_beat else "dwell"
        if k == 0:
            mode = "open"
        prev_beat = beat
        L.append(f"\n---\n## page {k + 1} · {mode} · arc={beat} of {B} · "
                 f"node=control · {len(body.split())}w\n")
        L.append(body)
    L.append(f"\n---\npages={N} cost=$0 complete=True")
    out = OUT / name
    out.write_text("\n".join(L), encoding="utf-8")
    return out


MAGI = {
    "prot": "Della", "cast": "Jim — her husband; Madame Sofronie — hair buyer",
    "palette": "tenderness — closeness held gently; sadness — loss settling in; "
               "joyful activation — bright forward energy",
    "spine": "The Flat -> The Hair -> The Chain -> The Gifts",
    "premise": "Della wants to give Jim a Christmas gift worthy of her love for "
               "him, but she has one dollar and eighty-seven cents.",
}
MAGI_BEATS = [
    {"event": "Della counts her one dollar and eighty-seven cents and despairs "
              "of buying Jim a worthy gift.", "mood": "tenderness"},
    {"event": "Della sells her knee-length hair to Madame Sofronie for twenty "
              "dollars.", "mood": "sadness", "price": "her hair"},
    {"event": "Della hunts the shops and buys the platinum fob chain for Jim's "
              "watch.", "mood": "joyful activation"},
    {"event": "Jim comes home, stunned by her shorn head — he has sold his "
              "watch to buy her the tortoise-shell combs.",
     "mood": "sadness", "price": "both gifts made useless"},
    {"event": "They put the gifts away; of all who give, these two are the "
              "wisest.", "mood": "tenderness"},
]
FIRE = {
    "prot": "the man", "cast": "the dog — a wolf-husky; the old-timer of Sulphur "
            "Creek — remembered advice",
    "palette": "peacefulness — stillness after motion; tension — a string "
               "tightening; sadness — loss settling in",
    "spine": "The Yukon Trail -> Henderson Creek -> The Spruce Fire -> The Camp",
    "premise": "A newcomer sets out alone across the Yukon at seventy-five below "
               "to reach camp by dark, against the old-timer's warning never to "
               "travel alone after fifty below.",
}
FIRE_BEATS = [
    {"event": "The man turns off the main trail at dawn, taking the cold as a "
              "fact to be managed, not feared.", "mood": "peacefulness"},
    {"event": "His spittle crackles in the air — colder than fifty below — and "
              "he walks on with the dog, planning lunch at the forks.",
     "mood": "tension"},
    {"event": "He breaks through a hidden spring and wets himself to the knees.",
     "mood": "tension", "price": "wet feet at seventy-five below"},
    {"event": "His first fire catches, then snow from the spruce bough snuffs "
              "it; the old-timer at Sulphur Creek was right.",
     "mood": "sadness", "price": "his one safe chance"},
    {"event": "His freezing hands fail match after match and every scheme, and "
              "panic sends him running down the trail.",
     "mood": "sadness", "price": "the use of his hands"},
    {"event": "He sits against the tree and meets death with dignity; the dog "
              "trots on toward the camp and the other food-providers.",
     "mood": "peacefulness", "price": "his life"},
]


GUTENBERG = {
    "gut_fourmillion.txt": "https://www.gutenberg.org/cache/epub/7256/pg7256.txt",
    "gut_lostface.txt": "https://www.gutenberg.org/cache/epub/2429/pg2429.txt",
}


def _fetch_sources() -> None:
    import urllib.request
    for name, url in GUTENBERG.items():
        p = HERE / name
        if not p.exists():
            print(f"fetching {name} …")
            urllib.request.urlretrieve(url, p)


def main() -> None:
    judge = "--judge" in sys.argv
    _fetch_sources()
    magi_txt = extract(HERE / "gut_fourmillion.txt",
                       r"(?i)THE GIFT OF THE MAGI", r"\*\*\*\s*END|\Z")
    fire_txt = extract(HERE / "gut_lostface.txt",
                       r"TO BUILD A FIRE", r"\nTHAT SPOT\n|\nTHAT SPOT\s")
    print(f"magi: {len(magi_txt.split())}w · fire: {len(fire_txt.split())}w")
    built = []
    for nm, ttl, hdr, beats, txt in (
            ("path_test_control_magi.md", "The Gift of the Magi (O. Henry, 1905)",
             MAGI, MAGI_BEATS, magi_txt),
            ("path_test_control_fire.md", "To Build a Fire (Jack London, 1908)",
             FIRE, FIRE_BEATS, fire_txt)):
        pages = paginate(txt)
        built.append(build(nm, ttl, hdr, beats, pages))
        built.append(build(nm.replace(".md", "_shuffled.md"), ttl, hdr, beats,
                           pages, shuffle=True))
    for b in built:
        print("built", b.name)
        if judge:
            subprocess.run([sys.executable, str(HERE / "score_story.py"),
                            str(b), "--judge"], check=False)


if __name__ == "__main__":
    main()
