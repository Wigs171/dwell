"""controls.py — literary controls for the judge (dev tooling).

Builds control files in the test-story format from PUBLIC-DOMAIN short stories:
  positive: the real story, true plan header   -> rubric must score it HIGH
  negative: same pages, order shuffled         -> connected must TANK
  SYNTHETIC NEGATIVES (r7): one seeded mechanical corruption per new criterion,
    each aimed to tank ITS criterion and hold the others (esp. prose):
      name_swap   (Magi)  -> continuity           (a name changes mid-story)
      unwind      (Fire)  -> consequences_persist  (a paid price is undone)
      strip_interior(Magi)-> prot_interior         (inner life deleted → a camera)
A criterion is only TRUSTED once its corruption tanks it specifically. Run:
    python controls.py                 # build all control files into stories/
    python controls.py --judge         # ...and judge them (r6)
    python controls.py --judge --rubric r7   # judge under r7 (validation gate)
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


# ---- synthetic corruptions: seeded mechanical text transforms -------------
# Each returns NEW pages; the transform is deterministic (a fixed seed / fixed
# rule) so a control is byte-reproducible. Each aims to tank exactly ONE r7
# criterion while leaving prose (grammar) and the others intact.

def corrupt_name_swap(pages: list[str], header: dict, beats: list[dict]) -> list[str]:
    """CONTINUITY killer: from the midpoint on, the protagonist's first name
    silently becomes a different name — a fact that contradicts the first half."""
    prot = re.split(r"[\s—,]", header["prot"].strip())[0]
    if not prot or not prot[0].isupper():          # unnamed prot ("the man") — skip
        return pages
    swap = "Marta" if prot != "Marta" else "Nadia"
    out, mid = [], len(pages) // 2
    for i, p in enumerate(pages):
        if i >= mid:
            p = re.sub(rf"\b{re.escape(prot)}\b", swap, p)
        out.append(p)
    return out


def corrupt_unwind(pages: list[str], header: dict, beats: list[dict]) -> list[str]:
    """CONSEQUENCES_PERSIST killer: a paid price is silently undone. Injects a
    blunt, PRICE-SPECIFIC reversal into the last two pages (a single vague
    sentence was noise against a long story — v1 left the criterion at 2). Works
    best on a SHORT control (Magi, 5pp) where the reversal is a big fraction."""
    priced = [b for b in beats if b.get("price") and b["price"].lower() != "none"]
    if not priced or len(pages) < 2:
        return pages
    price = priced[0]["price"].rstrip(". ")
    reversal = (f"None of it lasted. {price[0].upper() + price[1:]} — undone by "
                f"morning, made whole again as if the loss had never happened, "
                f"every cost repaid and nothing changed at all. ")
    out = pages[:]
    for i in range(len(out) - 2, len(out)):
        out[i] = reversal + out[i]
    return out


# inner-life lexicon — feeling, knowing, wanting, reacting (prot_interior strip)
_INNER = re.compile(
    r"\b(felt|feel|feels|feeling|knew|know|knows|knowing|thought|thinks|thinking|"
    r"wondered|wonder|wonders|hoped|hope|hopes|prayed|prays|longed|longs|yearned|"
    r"feared|fear|fears|dreaded|dreads|joy|joyous|joyful|grief|grieved|heart|"
    r"dizzy|ecstasy|worship|worshipped|adore|adored|adores|despair|happy|glad|sad|"
    r"sadly|sobbed|sobs|wept|weeping|weep|sighed|sighs|trembled|shivered|mind|soul|"
    r"wished|wish|wishes|loved|love|loves|tears|cry|cried|cries|proud|pride|"
    r"ashamed|shame|nervous|anxious|eager|delight|delighted|longing|content|"
    r"restless|weary|calm|afraid|terror|thrill|thrilled|ache|ached)\b", re.I)


def corrupt_strip_interior(pages: list[str], header: dict, beats: list[dict]) -> list[str]:
    """PROT_INTERIOR killer: delete every sentence carrying inner life →  a
    camera with a name. Keeps ≥2 sentences per page so grammar/plot survive
    (prose must hold); v1's 6-word lexicon deleted too little and the criterion
    stayed at 2, so the lexicon is much wider and the cut unconditional."""
    out = []
    for p in pages:
        sents = re.split(r"(?<=[.!?])\s+", p)
        kept = [s for s in sents if not _INNER.search(s)]
        if len(kept) < 2:                    # never gut a page to nothing
            kept = (kept + [s for s in sents if _INNER.search(s)])[:2]
        out.append(" ".join(kept))
    return out


def corrupt_flatten(pages: list[str], header: dict, beats: list[dict]) -> list[str]:
    """PROT_INTERIOR killer, aggressive: keep ONLY exterior sentences — cut any
    sentence with an inner-state word OR an evaluative/reflective clause, leaving
    a camera. On MAGI this failed (O.Henry's interiority is situational, not
    keyword-carried); it bites harder on Dwell prose, which names feeling
    explicitly (StoryScope tell #3). Keeps ≥2 sentences/page so grammar holds."""
    reflect = re.compile(
        r"\b(as if|as though|seemed to|somehow|deep (?:down|within)|in (?:her|his|"
        r"their|my) (?:chest|heart|mind|throat|gut|bones)|a part of (?:her|him|them)|"
        r"could not help|meant (?:everything|nothing|something)|for the first time)\b",
        re.I)
    out = []
    for p in pages:
        sents = re.split(r"(?<=[.!?])\s+", p)
        kept = [s for s in sents if not _INNER.search(s) and not reflect.search(s)]
        if len(kept) < 2:
            kept = (kept + [s for s in sents if s not in kept])[:2]
        out.append(" ".join(kept))
    return out


def corrupt_cast_merge(pages: list[str], header: dict, beats: list[dict]) -> list[str]:
    """CAST_DISTINCT killer: collapse the second named cast member's VOICE into
    the first's — every dialogue turn attributed to B is overwritten with one of
    A's lines (B parrots A), so the two are no longer distinguishable. Needs ≥2
    named cast + quoted dialogue; returns pages unchanged if it can't find them."""
    names = [re.split(r"[\s—,]", c.strip())[0] for c in header.get("cast", "").split(";")]
    names = [n for n in names if n and n[0].isupper()]
    if len(names) < 2:
        return pages
    a, b = names[0], names[1]
    text = "\n\n".join(pages)
    # collect A's quoted lines (a said "…" / "…" said A / "…," A said)
    a_lines = re.findall(rf'"([^"]{{8,}})"[^"]{{0,20}}\b{re.escape(a)}\b'
                         rf'|\b{re.escape(a)}\b[^"]{{0,20}}"([^"]{{8,}})"', text)
    a_flat = [x for pair in a_lines for x in pair if x]
    if not a_flat:
        return pages
    # overwrite B's quoted lines with A's, cycling
    ctr = {"i": 0}
    def repl(m):
        line = a_flat[ctr["i"] % len(a_flat)]
        ctr["i"] += 1
        return m.group(0).replace(m.group(1), line)
    text2 = re.sub(rf'"([^"]{{8,}})"(?=[^"]{{0,20}}\b{re.escape(b)}\b)', repl, text)
    text2 = re.sub(rf'(?<=\b{re.escape(b)}\b)([^"]{{0,20}}")([^"]{{8,}})(")',
                   lambda m: m.group(1) + a_flat[ctr["i"] % len(a_flat)] + m.group(3)
                   if not ctr.update(i=ctr["i"]+1) else "", text2)
    return text2.split("\n\n")


def corrupt_voice_merge(pages: list[str], header: dict, beats: list[dict]) -> list[str]:
    """VOICES_DISTINCT killer (epistolary): homogenize the two correspondents —
    overwrite the SECOND writer's letter bodies with the FIRST's, so the reply
    has no voice of its own. Splits on letter salutations; needs ≥2 letters."""
    names = [re.split(r"[\s—,]", c.strip())[0] for c in header.get("cast", "").split(";")]
    names = [n for n in names if n and n[0].isupper()]
    if len(names) < 2:
        return pages
    # in these controls the two writers alternate; make every EVEN letter a copy
    # of the previous ODD letter's body (same voice, different addressee)
    out = []
    prev_body = ""
    for p in pages:
        # a letter block = salutation line + body; keep the salutation, swap body
        lines = p.split("\n", 1)
        if len(lines) == 2 and prev_body:
            out.append(lines[0] + "\n" + prev_body)
        else:
            out.append(p)
        prev_body = p.split("\n", 1)[-1]
    return out


CORRUPTIONS = {
    "name_swap": corrupt_name_swap,
    "unwind": corrupt_unwind,
    "strip_interior": corrupt_strip_interior,
    "flatten": corrupt_flatten,
    "cast_merge": corrupt_cast_merge,
    "voice_merge": corrupt_voice_merge,
}


def build(name: str, title: str, header: dict, beats: list[dict],
          pages: list[str], shuffle: bool = False, corrupt: str = "") -> Path:
    if shuffle:
        pages = pages[:]
        random.Random(13).shuffle(pages)
    if corrupt:
        pages = CORRUPTIONS[corrupt](pages[:], header, beats)
    tag = ("control-shuf" if shuffle else
           f"control-{corrupt}" if corrupt else "control")
    B, N = len(beats), len(pages)
    L = [f"# control — {title}"
         + (" (SHUFFLED)" if shuffle else f" (CORRUPT: {corrupt})" if corrupt else ""),
         f"PV: {tag}", "FORM: story",
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


# which corruption controls each classic ships (a corruption only where its
# target criterion is testable on that text — Fire's man is unnamed, so
# continuity/interior corruptions use the named, interior-rich Magi)
CONTROL_CORRUPTIONS = {
    # all three on Magi: short (5pp) so a mechanical corruption is a big fraction
    # of the text, named prot (name_swap), and interior-rich (strip_interior).
    # Fire's man is unnamed and deliberately un-interior; its masterpiece-grade
    # persistence resisted a mechanical unwind (v1 left the criterion at 2).
    "path_test_control_magi.md": ["name_swap", "strip_interior", "unwind"],
    "path_test_control_fire.md": [],
}


_PAGE_RE = re.compile(r"^## page (\d+) · (\w+) · (.+?) · (\d+)w\s*$", re.M)


def corrupt_file(src: Path, corruption: str) -> Path:
    """RELATIVE control: apply a corruption to an existing Dwell story .md, keeping
    its plan header and page headers, writing <stem>_<corruption>.md (pv retagged).
    The judge compares clean-vs-corrupted (same plan) — the criterion is promoted
    if the target tanks and the untouched criteria hold. Used for corruptions that
    have no classic control (a story needs distinct cast / two letter-writers)."""
    text = src.read_text(encoding="utf-8", errors="replace")
    header = {"cast": ""}
    for line in text.splitlines()[:40]:
        if line.startswith("CAST:"):
            header["cast"] = line[5:].strip()
        if line.startswith("PROTAGONIST:"):
            header["prot"] = line[12:].strip()
    heads = list(_PAGE_RE.finditer(text))
    bodies, spans = [], []
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        body = text[h.end():end]
        body = re.sub(r"\n---\s*$", "", body.split("\npages=")[0]).strip()
        bodies.append(body)
        spans.append((h.end(), end))
    new_bodies = CORRUPTIONS[corruption](bodies, header, [])
    # rebuild: splice corrupted bodies back into their spans (reverse order)
    out = text
    for (a, b), nb in sorted(zip(spans, new_bodies), key=lambda t: -t[0][0]):
        out = out[:a] + "\n" + nb + "\n" + out[b:]
    out = re.sub(r"^PV:.*$", f"PV: control-{corruption}", out, count=1, flags=re.M)
    dst = OUT / (src.stem + f"_{corruption}.md")
    dst.write_text(out, encoding="utf-8")
    print(f"corrupt-file [{corruption}] {src.name} -> {dst.name} "
          f"({len(bodies)} pages)")
    return dst


def main() -> None:
    if "--corrupt-file" in sys.argv:
        i = sys.argv.index("--corrupt-file")
        src, cor = Path(sys.argv[i + 1]), sys.argv[i + 2]
        if not src.is_absolute():
            src = OUT / src.name
        d = corrupt_file(src, cor)
        if "--judge" in sys.argv:
            rub = sys.argv[sys.argv.index("--rubric") + 1] if "--rubric" in sys.argv else "r7"
            subprocess.run([sys.executable, str(HERE / "score_story.py"), str(d),
                            "--judge", "--rubric", rub], check=False,
                           env=dict(__import__("os").environ, PYTHONIOENCODING="utf-8"))
        return
    judge = "--judge" in sys.argv
    rubric = "r6"
    if "--rubric" in sys.argv:
        rubric = sys.argv[sys.argv.index("--rubric") + 1]
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
        for cor in CONTROL_CORRUPTIONS.get(nm, []):
            built.append(build(nm.replace(".md", f"_{cor}.md"), ttl, hdr, beats,
                               pages, corrupt=cor))
    for b in built:
        print("built", b.name)
        if judge:
            cmd = [sys.executable, str(HERE / "score_story.py"), str(b),
                   "--judge", "--rubric", rubric]
            subprocess.run(cmd, check=False,
                           env=dict(__import__("os").environ, PYTHONIOENCODING="utf-8"))


if __name__ == "__main__":
    main()
