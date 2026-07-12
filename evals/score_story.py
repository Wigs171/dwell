"""score_story.py — dev-only eval harness for generated path stories/pages.

NOT part of the app: no server/UI wiring. It scores the test-harness output
files (dota_gen_story.py format) on the criteria we've been eyeballing all
along, so prompt changes become a scorecard diff instead of prose impressions.

    python score_story.py <story.md> [--vault PATH] [--judge] [--pv p16]
                          [--form story] [--no-log] [--history PATH]

Three layers, each degrading gracefully when its inputs are absent:
  L1  mechanical   $0, deterministic — POV/cast/ghost-names/mood-leak/slop/clones
  L2  embedding    $0, needs --vault — mood-match + valence arc (Reagan-style)
  L3  judge        ~$0.01-0.03, needs --judge + ANTHROPIC_API_KEY — the semantic
                   criteria (event STAGED vs described, price LANDED, ending
                   SETTLED, protagonist CHANGED), evidence-quote forced.
Judge model is deliberately a different family than the renderer (no grading
your own homework). Rubrics dispatch on the form's class:
  enacted   story/case/epistolary   — plan-execution criteria
  didactic  tutorial/guided/qa/brief — lesson/syllabus criteria
  expository everything else        — flow/connection criteria
Scores land in history.jsonl keyed by prompt version → trend over time.
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTO = HERE.parent / "server"          # repo layout; dev layout = prototypes/
if not PROTO.exists():
    PROTO = HERE.parent
sys.path.insert(0, str(PROTO))

ENACTED = ("story", "case", "epistolary")
DIDACTIC = ("tutorial", "guided", "qa", "brief")
JUDGE_MODEL = "claude-haiku-4-5-20251001"
RUBRICS = ("r6", "r7")
DEFAULT_RUBRIC = "r6"    # NEVER auto-bump: a rubric switch must not slide under a
                        # running A/B (judge scores don't compare across versions).
                        # r7 ships behind --rubric r7 and becomes default only after
                        # its controls pass (see controls.py corruptions).
RUBRIC_V = DEFAULT_RUBRIC
# criteria the judge still ASKS but that are EXCLUDED from judge_score (reported
# in the story dict as signal, not scored) — the London control: his
# deliberately unnamed man is a masterpiece, not a defect, so named_on_page is a
# diagnostic, never a quality axiom. mood_leaks lives in L1 (already outside
# judge_score); it's listed here so the split is one source of truth.
DIAGNOSTIC_CRITERIA = {
    "r6": set(),
    "r7": {"named_on_page", "mood_leaks",
           # PROVISIONAL — reported but NOT scored until a targeted corruption
           # tanks each specifically (the trust rule). Validation gate 2026-07-09:
           #   continuity          — VALIDATED (name_swap 2→1) → SCORED
           #   consequences_persist— VALIDATED (unwind 2→0)   → SCORED
           #   prot_interior       — FAILED: no corruption tanks it (O.Henry's
           #       interiority survives keyword deletion) — DIAGNOSTIC until a
           #       genuinely flat negative (plot-summary rewrite / camera-story) works
           #   cast_distinct       — no control yet (needs multi-voice control)
           #   voices_distinct     — no control yet (needs an epistolary control)
           "prot_interior", "cast_distinct", "voices_distinct"},
}
# RUBRIC HISTORY (judge scores NEVER compare across versions):
# r7: THREE-LAYER rubric behind --rubric (default r6). (1) diagnostic/quality
#     split — named_on_page + mood_leaks reported, EXCLUDED from judge_score
#     (the unnamed-man-is-not-a-defect lesson). (2) enacted story gains
#     continuity/consequences_persist/prot_interior/cast_distinct;
#     premise_resolved + mood_coherent demoted (dup of ending_settled / covered
#     by L2 mood_match + L1 mood_leaks). (3) FORM OVERLAYS generated off the
#     FORMS[] contract text (one source of truth for render AND judge):
#     epistolary/case/dialogue/debate/chronicle/interview. + L1 fk_grade
#     (readability, informational — never in judge_score).
# r6: page-level `prose` criterion (a future-tense prophecy story with "its its"
#     scored 93 — the structural rubric was blind to register/grammar).
                         # r5: theme_embodied added (concept-vault stories staged ideas as
                         # figures LECTURING the protagonist — the MPH mentor-museum tour;
                         # ideas should happen TO the protagonist as events)
                         # r4: connection measured at the SEAMS — r3's order-sensitive
                         # wording still failed the shuffle controls (the judge mentally
                         # un-shuffles a strong story and reports ITS causality); each
                         # judged page now sees the previous page's real ENDING and
                         # answers follows_prev — falsifiable, shuffle-breaks-seams
                         # r2: no_busywork split into busywork_steps + stays_on_promise
                         # (they were conflated — digressions read as "busywork");
                         # named_on_page added (both judge tiers accepted pronoun-only
                         # protagonists); low-dream tours route to the expository rubric
                         # r3: `connected` made ORDER-SENSITIVE (the shuffled-classic
                         # negative controls scored connected=2 — it measured topic
                         # vibes, not causal structure) — now demands a page that
                         # cannot move without breaking what follows

# the anti-slop checklist, measurable form (dwell.py _RULES + the tell lexicon)
SLOP = ["delve", "tapestry", "crucially", "it's worth noting", "it is worth noting",
        "stands as a testament", "testament to", "reminds us that", "tie together",
        "weave together", "underscores", "highlights the importance"]
META = ["this page", "this section", "as we saw", "earlier we", "in the next chapter",
        "in the previous chapter", "as mentioned", "in conclusion"]

VAL_POS = "joy, warmth, hope, tenderness, wonder, delight, peace, triumph"
VAL_NEG = "grief, dread, fear, loss, despair, menace, sorrow, defeat"


# --------------------------------------------------------------------------- parse
# bridge arcs contain " · " themselves ("arc=tween 1 · A → B"), so the header
# is matched loosely and its middle is walked segment-by-segment instead
PAGE_RE = re.compile(r"^## page (\d+) · (\w+) · (.+?) · (\d+)w\s*$", re.M)


def _parse_mid(mid: str) -> dict:
    out = {"arc": "", "node": "", "waypoint": ""}
    key = None
    for seg in mid.split(" · "):
        if seg.startswith("arc="):
            key, out["arc"] = "arc", seg[4:]
        elif seg.startswith("node="):
            key, out["node"] = "node", seg[5:]
        elif seg.startswith("WAYPOINT="):
            key, out["waypoint"] = "waypoint", seg[9:]
        elif key:                        # continuation of the previous field
            out[key] += " · " + seg
    return out
BEAT_RE = re.compile(r"^(\d+)\.\s+(?:\[(\d)p\]\s+)?(.*?)"
                     r"(?:\s+\(mood:\s*([^)]+)\))?(?:\s+\(price:\s*([^)]+)\))?\s*$")


def parse_story(text: str) -> dict:
    s: dict = {"pages": [], "beats": [], "cast": [], "palette": [], "moods": [],
               "protagonist": "", "premise": "", "pv": "", "form": "", "spine": []}
    for line in text.splitlines()[:40]:
        line = line.strip()
        for key, field in (("PROTAGONIST:", "protagonist"), ("PREMISE:", "premise"),
                           ("PV:", "pv"), ("FORM:", "form")):
            if line.startswith(key):
                s[field] = line[len(key):].strip()
        if line.startswith("CAST:"):
            s["cast"] = [c.strip() for c in line[5:].split(";") if c.strip()]
        if line.startswith("PALETTE:"):
            # entries are "name — gloss" ";"-joined, but glosses may hold ";"
            # themselves — a fragment without "—" belongs to the previous gloss
            ents: list[str] = []
            for part in line[8:].split(";"):
                part = part.strip()
                if not part:
                    continue
                if "—" in part or not ents:
                    ents.append(part)
                else:
                    ents[-1] += "; " + part
            s["palette"] = ents
        if line.startswith("spine:"):
            s["spine"] = [re.sub(r"\[\w+\]$", "", t.strip())
                          for t in line[6:].split("->")]
    if s["protagonist"].startswith("(none"):
        s["protagonist"] = ""            # factual tour — no viewpoint to score
    in_plot = False
    for line in text.splitlines():
        if line.startswith("## THE PLOT"):
            in_plot = True
            continue
        if in_plot:
            if line.startswith(("---", "## page")):
                break
            m = BEAT_RE.match(line.strip())
            if m and m.group(3):
                s["beats"].append({"i": int(m.group(1)) - 1,
                                   "weight": int(m.group(2) or 1),
                                   "event": m.group(3).strip(),
                                   "mood": (m.group(4) or "").strip(),
                                   "price": (m.group(5) or "").strip()})
    heads = list(PAGE_RE.finditer(text))
    beat_i = 0                       # bridges inherit the departing gate's beat
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        body = text[h.end():end]
        body = re.sub(r"\n---\s*$", "", body.split("\npages=")[0]).strip()
        mode = h.group(2)
        mid = _parse_mid(h.group(3).strip())
        ma = re.match(r"(\d+) of (\d+)", mid["arc"])
        if ma and mode in ("open", "move", "dwell"):
            beat_i = int(ma.group(1)) - 1
        s["pages"].append({"n": int(h.group(1)), "mode": mode, "arc": mid["arc"],
                           "node": mid["node"], "waypoint": mid["waypoint"],
                           "words": int(h.group(4)), "body": body,
                           "beat": beat_i, "gate": mode in ("open", "move")})
    return s


def form_class(form: str) -> str:
    if form in ENACTED:
        return "enacted"
    if form in DIDACTIC:
        return "didactic"
    return "expository"


def first_name(full: str) -> str:
    return re.split(r"[\s—,]", full.strip())[0] if full.strip() else ""


# --------------------------------------------------------------------- moralizing coda
# StoryScope's #1 remaining Dwell AI-tell (2026-07-09 fingerprint): the narrator
# STATES the story's theme — 70% of Dwell stories vs 52% human baseline (frontier
# AI 100%). A moralizing coda is a thematic-summary at the very end: a lesson-verb
# clause ("she understood that…"), a meta-lesson phrase ("the lesson was…"), a
# not-X-but-Y aphorism, or an abstract theme-noun pronounced as a general truth
# in the closing sentences. Detector = mechanical, story-level, FINAL page only.
# NB: this legitimately fires on Magi's famous human coda ("of all who give…the
# wisest") — hence the target is 52%, not 0%; a human writer may earn a coda.
_THEME_NOUN = (
    r"love|grief|hope|loss|truth|freedom|courage|faith|memory|memories|time|life|"
    r"death|fear|home|silence|sacrifice|forgiveness|meaning|purpose|destiny|fate|"
    r"sorrow|joy|peace|wisdom|longing|belonging|redemption|grace|mercy|justice|"
    r"power|beauty|change|journey|lesson|price|cost|heart|soul|world|past|future|"
    r"light|darkness|kindness|cruelty|trust|betrayal|regret|guilt|shame|pride|"
    r"honou?r|dignity|survival|endurance|patience|understanding|desire|ambition")
_LESSON_VERB = (
    r"learned|learnt|understood|realized|realised|knew|discovered|grasped|"
    r"came to see|came to understand|came to know|comes to see|understands|"
    r"realizes|realises|knows now|taught (?:her|him|them|us|me|it)")


def _closing_sentences(text: str, k: int = 4) -> list[str]:
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x.strip()]
    return sents[-k:]


def _mostly_past(text: str) -> bool:
    past = len(re.findall(r"\b(was|were|had|\w+ed)\b", text, re.I))
    pres = len(re.findall(r"\b(is|are|has|have|does|do)\b", text, re.I))
    return past >= pres


def lesson_stated(pages: list[dict]) -> tuple[bool, str]:
    """(flag, matched-tell-tags) — does the story END by stating its theme?
    Looks only at the FINAL page's closing sentences. Tuned for precision:
    a concrete final image/action does not fire; a generalization does."""
    if not pages:
        return False, ""
    final = pages[-1]["body"]
    close = _closing_sentences(final, 4)
    if not close:
        return False, ""
    low = " ".join(close).lower()
    hits: list[str] = []
    # 1. reflective lesson: a lesson-verb soon followed by a "that" clause
    if re.search(rf"\b(?:{_LESSON_VERB})\b[^.?!]{{0,55}}\bthat\b", low):
        hits.append("learned-that")
    # 2. explicit meta-lesson: naming the lesson/moral/truth, or a labeled coda,
    #    or the O.Henry-style "of all who … the wisest" comparative pronouncement
    if re.search(r"(\blesson\s*:|\*lesson\*|\bthe (?:lesson|moral)\b|"
                 r"\bwhat it (?:all )?meant\b|\bwhat (?:really )?mattered\b|"
                 r"\bthe (?:real |whole )?(?:truth|point|meaning) (?:was|is)\b|"
                 r"\bof all (?:who|that|those)\b[^.?!]{0,70}\b\w{3,}est\b|"
                 r"\bthis was what\b)", low):
        hits.append("meta-lesson")
    # 3. not-X-but-Y aphorism in the close
    if re.search(r"\bnot (?:about |merely |just |simply |only )?[\w'-]+,? but\b", low):
        hits.append("not-but")
    # 4. gnomic pronouncement: a closing sentence OPENS on a theme-noun as subject
    #    of a GENERALIZING predicate (metaphor equation / negation / always-never)
    #    — a bare concrete "the memory is sealed" is excluded by the complement set
    _subj = rf"^(?:perhaps |maybe |and so,? |in the end,? |but )?(?:the |a )?(?:{_THEME_NOUN})\b\s+"
    _pred = (r"(?:is|was|means|meant|is not|isn't)\s+"
             r"(?:not |always |never |only |nothing|everything|a |an |the |what |how |where |when |to )")
    _pred2 = (r"(?:would always|would never|never (?:truly |really )?(?:dies|ends|leaves|fades)"
              r"|always (?:returns|remains|wins))")
    _gnomic = re.compile(_subj + r"(?:" + _pred + r"|" + _pred2 + r")", re.I)
    for st in close:
        if _gnomic.match(st.strip()):
            hits.append("gnomic")
            break
    return (len(hits) >= 1, ";".join(hits))


# --------------------------------------------------------------------------- L1
def _ngrams(words: list[str], n: int = 5) -> set[tuple]:
    return {tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def _syllables(word: str) -> int:
    """Heuristic syllable count (no deps): vowel groups, drop a silent final
    'e', floor 1. Good enough for a readability trend, not linguistics."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ie", "ee")) and groups > 1:
        groups -= 1
    return max(1, groups)


def _fk_grade(text: str) -> float:
    """Flesch-Kincaid Grade Level = .39·(words/sentences) + 11.8·(syll/words) − 15.59."""
    sents = max(1, len(re.findall(r"[.!?]+", text)))
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return 0.0
    syll = sum(_syllables(w) for w in words)
    g = 0.39 * (len(words) / sents) + 11.8 * (syll / len(words)) - 15.59
    return round(g, 1)


def layer1(s: dict, fclass: str, vault_titles: set[str]) -> dict:
    pages = s["pages"]
    bodies = [p["body"] for p in pages]
    lower = [b.lower() for b in bodies]
    out: dict = {}
    # -- universal hygiene ---------------------------------------------------
    out["slop_hits"] = sum(l.count(tok) for l in lower for tok in SLOP)
    # PROSE SANITY — glitch detectors the structural rubric is blind to:
    # doubled words ("its its edge") and prophecy-register density ("will X"
    # sentences — a story narrated in future tense is not a book)
    _totw = sum(p["words"] for p in pages) or 1
    out["doubled_words"] = sum(
        len(re.findall(r"\b([a-z']+) \1\b", l)) for l in lower)
    _will = sum(len(re.findall(r"\bwill [a-z]+", l)) for l in lower)
    out["will_per_1kw"] = round(1000 * _will / _totw, 1)
    out["meta_hits"] = sum(l.count(tok) for l in lower for tok in META)
    # FK GRADE — Flesch-Kincaid grade level, $0, no deps. INFORMATIONAL (never
    # in judge_score): a level-fidelity signal — a "child" render should read
    # lower-grade than a "scholar" one. Syllables via vowel-group heuristic.
    out["fk_grade"] = _fk_grade(" ".join(bodies))
    grams = [_ngrams(re.findall(r"[a-z']+", l)) for l in lower]
    worst = 0.0
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            if grams[i] and grams[j]:
                jac = len(grams[i] & grams[j]) / len(grams[i] | grams[j])
                worst = max(worst, jac)
    out["clone_max"] = round(worst, 3)
    opens = [tuple(re.findall(r"[a-z']+", l)[:8]) for l in lower]
    out["opening_dupes"] = sum(1 for i in range(len(opens))
                               for j in range(i + 1, len(opens))
                               if opens[i] and len(set(opens[i]) & set(opens[j])) >= 6)
    # the needle is the page's ACTUAL last 60 chars — an earlier -90..-31
    # window systematically missed short final-sentence echoes (found live:
    # "The tide will not wait." closing page 9 and opening page 10)
    out["tail_echoes"] = sum(
        1 for i in range(len(bodies) - 1)
        if len(bodies[i]) > 120
        and bodies[i].strip().lower()[-60:] in lower[i + 1])
    out["avg_words"] = round(sum(p["words"] for p in pages) / max(1, len(pages)))
    # THEMATIC/PHRASE ECHO — verbatim 6-grams recurring on 3+ pages. Distinct
    # from clone_max (whole-page pairwise overlap): this catches a single pet
    # phrase (a lecturer's spoken formula, a premise line restated) stamped
    # across the story. Reported as the actual phrases, so the offender is
    # readable, not just counted.
    from collections import Counter as _C
    gram_pages: dict = {}
    for i, l in enumerate(lower):
        ws = re.findall(r"[a-z']+", l)
        for g in {tuple(ws[k:k + 6]) for k in range(max(0, len(ws) - 5))}:
            gram_pages.setdefault(g, set()).add(i)
    echoes = {g: ps for g, ps in gram_pages.items() if len(ps) >= 3}
    # collapse overlapping grams: keep a gram only if no superset gram covers
    # the same pages (crude — drop grams sharing 5 words with a kept one)
    kept: list[tuple] = []
    for g in sorted(echoes, key=lambda g: -len(echoes[g])):
        if not any(len(set(g) & set(k)) >= 5 for k in kept):
            kept.append(g)
    out["phrase_echoes"] = [{"phrase": " ".join(g), "pages": len(echoes[g])}
                            for g in kept[:6]]
    # CITED SPEAKERS — "X says/said/reminds/teaches/writes" where X is a
    # TitleCase name that is neither protagonist nor cast: the source-voice
    # leaking into the output as an unexplained authority ("Hall says").
    known_speakers = {first_name(s.get("protagonist", "")).lower()} | \
                     {first_name(c).lower() for c in s.get("cast", [])} | \
                     {"she", "he", "they", "it", "we", "you", "the", "and",
                      "but", "then", "when", "someone", "nobody", "everyone"}
    cites: _C = _C()
    for b in bodies:
        for m in re.finditer(r"\b([A-Z][a-z]{2,})(?:'s)?\s+"
                             r"(?:says|said|say|reminds|reminded|teaches|taught|"
                             r"writes|wrote|tells|told|lectures|speaks|spoke)\b", b):
            if m.group(1).lower() not in known_speakers:
                cites[m.group(1)] += 1
    out["cited_speakers"] = dict(cites.most_common(4))
    # -- form-class criteria ---------------------------------------------------
    # CLOISTER DENSITY — the mystical-architecture monoculture indicator
    # (chamber-to-chamber revelation tours): occurrences per 1000 words.
    _cw = sum(len(re.findall(r"\bchambers?\b|\bcorridors?\b|\bhallways?\b"
                             r"|\bantechambers?\b|\bsanctums?\b|\bcloisters?\b"
                             r"|\blecture halls?\b", l)) for l in lower)
    _tw = sum(p["words"] for p in pages) or 1
    out["cloister_per_1kw"] = round(1000 * _cw / _tw, 1)
    if fclass == "enacted" and s["protagonist"]:
        pf = first_name(s["protagonist"]).lower()
        present = [pf in l for l in lower]
        # a FIRST- or SECOND-person telling rarely self-names — count the
        # narrating pronoun's pages as present
        for pat in (r"\bI\b", r"\byou\b|\byour\b"):
            dens = sum(len(re.findall(pat, b, re.I if "you" in pat else 0))
                       for b in bodies) / _tw * 100
            if dens > 1.2:
                present = [p or re.search(pat, b,
                                          re.I if "you" in pat else 0) is not None
                           for p, b in zip(present, bodies)]
        out["prot_presence"] = round(sum(present) / max(1, len(present)), 2)
        out["prot_on_page1"] = bool(present and present[0])
        if s["cast"]:
            names = [first_name(c).lower() for c in s["cast"]]
            names = [n for n in names if n and n != pf]
            out["cast_present"] = round(
                sum(1 for n in names if any(n in l for l in lower))
                / max(1, len(names)), 2)
        # ghost names: TitleCase tokens recurring across pages that are in NO
        # known-name set and never appear lowercase (real prose words do)
        known = {pf} | {first_name(c).lower() for c in s["cast"]}
        # everything the PLAN names is known: spine + waypoints + arc titles +
        # vault titles + the beat events and premise themselves (older files
        # have no CAST: header — a figure the plan introduces is never a ghost)
        # — word-level, with naive singular/plural stemming
        srcs = (s["spine"] + [p["waypoint"] for p in pages]
                + [p["arc"] for p in pages] + list(vault_titles)
                + [b["event"] for b in s["beats"]]
                + [s["premise"], s["protagonist"]] + s["cast"])
        for t in srcs:
            for w in re.findall(r"[A-Za-z']+", t or ""):
                known.add(w.lower())
                known.add(w.lower().rstrip("s"))
        known |= {w.rstrip("s") for w in list(known)}
        cand: Counter = Counter()
        for b in bodies:
            for m in re.finditer(r"(?<=[a-z,;] )([A-Z][a-z]{2,})\b", b):
                cand[m.group(1)] += 1
        # a real name never appears lowercase in the same text — words that do
        # are ordinary vocabulary that happened to start a clause
        alltext = " ".join(bodies)
        out["ghost_names"] = sorted(
            w for w, c in cand.items()
            if c >= 3 and w.lower() not in known
            and not re.search(rf"(?<![A-Za-z]){re.escape(w.lower())}(?![A-Za-z])",
                              alltext))
        # mood-leak: the assigned mood word surfacing in its own beat's prose
        leaks = 0
        for p in pages:
            b = s["beats"][p["beat"]] if p["beat"] < len(s["beats"]) else None
            if b and b["mood"]:
                mw = b["mood"].lower().split()[0]
                if len(mw) > 3 and re.search(rf"\b{re.escape(mw)}", p["body"].lower()):
                    leaks += 1
        out["mood_leaks"] = leaks
        # MORALIZING CODA (StoryScope's #1 remaining tell) — does the final page
        # STATE the theme? Reported as a boolean + the matched tell-tags.
        flag, tags = lesson_stated(pages)
        out["lesson_stated"] = flag
        if flag:
            out["lesson_tags"] = tags
    if fclass == "didactic":
        yous = sum(len(re.findall(r"\byou\b|\byour\b", l)) for l in lower)
        words = sum(p["words"] for p in pages) or 1
        out["second_person_per100w"] = round(100 * yous / words, 1)
    return out


# --------------------------------------------------------------------------- L2
def load_brain(vault: str):
    try:
        from dwell import Brain
        from compendium.vault.layout import VaultPaths
        return Brain.load(VaultPaths(Path(vault)), progress=lambda m: None)
    except Exception as e:
        print(f"[vault load failed: {str(e)[:80]}]", file=sys.stderr)
        return None


def layer2(s: dict, brain) -> dict:
    sp = brain.space if brain else None
    if sp is None:
        return {"skipped": "no space"}
    enc = sp.encode_text
    pos, neg = enc(VAL_POS), enc(VAL_NEG)
    out: dict = {}
    vals = []
    for p in s["pages"]:
        v = enc(p["body"][:2500])
        vals.append(round(sp.cos(v, pos) - sp.cos(v, neg), 4))
    out["valence"] = vals
    # planned low point = the heaviest-priced late beat; its pages should hold
    # the measured valley (the arc SHAPE criterion, Reagan-style)
    priced = [b for b in s["beats"] if b["price"] and b["price"].lower() != "none"]
    if priced and len(vals) > 2:
        low_beat = priced[0]["i"] if len(priced) == 1 else priced[-2]["i"]
        low_pages = [i for i, p in enumerate(s["pages"]) if p["beat"] >= low_beat]
        out["valley_in_fall"] = bool(low_pages) and (vals.index(min(vals)) in low_pages)
    # mood-match: each gate page should sit closer to ITS motif than the others
    # reference vectors use the FULL "name — gloss" entry: a bare 1-2 word name
    # vs a 400-word page is drowned by topical vocabulary (measured: 0.08 hit
    # rate, below the ~0.33 random floor); the gloss carries the affect signal
    entry = {pm.split("—")[0].strip(): pm for pm in s["palette"]}
    pal = list(entry) or sorted({b["mood"] for b in s["beats"] if b["mood"]})
    if len(pal) >= 2 and any(b["mood"] for b in s["beats"]):
        pvecs = {m: enc(entry.get(m, m)) for m in pal}
        # raw cosine is biased: one reference vector sits closer to EVERYTHING
        # in a single-setting story and wins every page. Z-normalize each
        # mood's scores ACROSS pages first — the question becomes "is this page
        # unusually close to its assigned mood, relative to the other pages".
        scored = []
        for p in s["pages"]:
            b = s["beats"][p["beat"]] if p["beat"] < len(s["beats"]) else None
            if b and b["mood"] and b["mood"] in pvecs:
                v = enc(p["body"][:2500])
                scored.append((b["mood"], {m: sp.cos(v, pvecs[m]) for m in pal}))
        if len(scored) >= 3:
            mu = {m: sum(r[m] for _, r in scored) / len(scored) for m in pal}
            sd = {m: (sum((r[m] - mu[m]) ** 2 for _, r in scored)
                      / len(scored)) ** 0.5 or 1e-9 for m in pal}
            hits = sum(1 for want, r in scored
                       if max(pal, key=lambda m: (r[m] - mu[m]) / sd[m]) == want)
            out["mood_match"] = round(hits / len(scored), 2)
            out["mood_baseline"] = round(1 / len(pal), 2)
    return out


# --------------------------------------------------------------------------- L3
def _api_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    for envp in (PROTO / ".env", PROTO.parent / ".env"):
        if envp.exists():
            for line in envp.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _judge_call(key: str, system: str, user: str,
                model: str = JUDGE_MODEL) -> dict | None:
    import requests
    # r2 asks for evidence PER criterion — 800 tokens truncated story-level
    # replies mid-JSON, which parsed to None with no error (silent data loss)
    payload = {"model": model, "max_tokens": 1600, "temperature": 0,
               "system": system, "messages": [{"role": "user", "content": user}]}
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=payload, timeout=120)
    if r.status_code == 400 and "temperature" in r.text:
        payload.pop("temperature")     # deprecated on newer models (Sonnet 5+)
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=payload, timeout=120)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


_SEAM = ('\nfollows_prev (null when no previous ending is quoted): does THIS page '
         "OPEN as a continuation of the PREVIOUS PAGE'S ENDING quoted in the input — "
         "same moment carried, or its direct consequence (2); a soft reset that "
         "restarts elsewhere but not impossibly (1); a break or contradiction of "
         "where things just stood (0). Judge ONLY the seam, not the whole story."
         '\nprose (add to the JSON: "prose": 0|1|2): could a published book carry '
         "these sentences? 2 = clean, natural prose; 1 = minor stumbles; 0 = broken "
         "grammar, doubled or dropped words, or an unnatural narrative register "
         "(e.g. a story narrated in future tense). Judge the WRITING, not the ideas.")

PAGE_RUBRIC = {
    "enacted": (
        'Score this PAGE of a serialized story against ITS PLANNED BEAT. Reply ONLY '
        'JSON: {"staged": 0|1|2, "price_landed": 0|1|2|null, "follows_prev": 0|1|2|null, '
        '"evidence": "<short quote>"}\n'
        "staged: 2 = the protagonist ACTS the planned event on the page (it happens, "
        "moment to moment); 1 = the event half-happens or is diluted into description; "
        "0 = the page describes the setting, retells background, or INVERTS the event "
        "(e.g. cooperation where the plan says defeat).\n"
        "price_landed (null when the beat lists no price): 2 = the stated price visibly "
        "lands ON the page in body/action/behavior; 1 = merely stated or implied; "
        "0 = absent." + _SEAM),
    "didactic": (
        'Score this PAGE of a serialized tutorial against ITS PLANNED LESSON. Reply ONLY '
        'JSON: {"taught": 0|1|2, "builds_on": 0|1|2, "follows_prev": 0|1|2|null, '
        '"evidence": "<short quote>"}\n'
        "taught: 2 = the page concretely teaches the planned lesson (a reader could now "
        "do/explain it); 1 = gestures at it; 0 = talks around it or teaches other things.\n"
        "builds_on: 2 = visibly stands on earlier pages' gains; 1 = weakly; 0 = isolated."
        + _SEAM),
    "expository": (
        'Score this PAGE of a serialized piece. Reply ONLY JSON: '
        '{"advances": 0|1|2, "grounded": 0|1|2, "follows_prev": 0|1|2|null, '
        '"evidence": "<short quote>"}\n'
        "advances: 2 = the page moves the through-line forward (new matter, connected "
        "back); 1 = adjacent matter loosely connected; 0 = re-treads or floats free.\n"
        "grounded: 2 = concrete and specific; 1 = mixed; 0 = generic filler." + _SEAM),
}
STORY_RUBRIC = {
    "enacted": (
        'Score this WHOLE STORY against its plan. Reply ONLY JSON: {"ending_settled": '
        '0|1|2, "prot_changed": 0|1|2, "connected": 0|1|2, "premise_resolved": 0|1|2, '
        '"mood_coherent": 0|1|2, "named_on_page": 0|1|2, "theme_embodied": 0|1|2, '
        '"evidence": {"<criterion>": "<short quote>", ...}}\n'
        "theme_embodied: how do the story's IDEAS arrive? 2 = enacted — things that "
        "HAPPEN to the protagonist carry the ideas, and the reader infers them from "
        "events; 1 = mixed; 0 = recited — sages, mentors, or voices explain the ideas "
        "to the protagonist (a lecture wearing a story).\n"
        "named_on_page: 2 = the protagonist is called by NAME in the prose, early and "
        "across the story; 1 = named once or twice then pronouns only; 0 = never named "
        "in the pages (pronoun-only viewpoint) — judge the PAGES, not the plan header.\n"
        "ending_settled: 2 = the central want is settled on the page, won or lost; 1 = "
        "half-settled or drifting close; 0 = deferred/atmospheric close. prot_changed: is "
        "the protagonist different at the end, shown not told? "
        "connected: judge CAUSAL ORDER, not shared topic — 2 = later pages DEPEND on "
        "earlier ones (your evidence must name a page that could not be moved without "
        "breaking what follows); 1 = a loose sequence, some consequences carried; 0 = "
        "the pages could be read in a different order with little loss — a shared "
        "setting or recurring vocabulary does NOT count as connection. "
        "premise_resolved: does the ending answer the premise's specific conflict? "
        "mood_coherent: one emotional through-line with purposeful turns (2) vs whiplash "
        "or monotone (0)."),
    "didactic": (
        'Score this WHOLE TUTORIAL against its promise. Reply ONLY JSON: '
        '{"promise_kept": 0|1|2, "progression": 0|1|2, "connected": 0|1|2, '
        '"busywork_steps": 0|1|2, "stays_on_promise": 0|1|2, '
        '"evidence": {"<criterion>": "<short quote>", ...}}\n'
        "promise_kept: by the end can the reader do what the promise offered? "
        "progression: do lessons stack (each standing on the last)? "
        "connected: judge DEPENDENCE, not shared topic — 2 = later lessons USE earlier "
        "gains (name one that could not come first); 0 = lessons could be taken in any "
        "order. busywork_steps: 2 = every step/task "
        "teaches something the material holds; 0 = fabricated practice tasks that serve "
        "no lesson. stays_on_promise: 2 = every page serves the promised skill; 0 = "
        "digressions (biography, tangents, register shifts) that abandon the syllabus. "
        "These are DIFFERENT failures — score them independently, evidence for each."),
    "expository": (
        'Score this WHOLE PIECE. Reply ONLY JSON: {"through_line": 0|1|2, "connected": '
        '0|1|2, "lands": 0|1|2, "evidence": "<short quote>"}\n'
        "through_line: one discernible line of thought start to finish? "
        "connected: judge DEPENDENCE, not shared topic — 2 = later pages build on what "
        "earlier ones established (name one that could not move without loss); 0 = the "
        "pages could be read in any order. lands: the final page concludes rather "
        "than stops."),
}

# ---- r7 -------------------------------------------------------------------
# The enacted STORY rubric under r7: four new craft criteria; premise_resolved +
# mood_coherent DROPPED (premise_resolved duplicated ending_settled; mood
# coherence is measured $0 by L2 mood_match + L1 mood_leaks). named_on_page is
# ASKED but tagged DIAGNOSTIC (excluded from judge_score — see DIAGNOSTIC_CRITERIA).
# Quality budget: 8 (ending_settled, prot_changed, connected, theme_embodied,
# continuity, consequences_persist, prot_interior, cast_distinct). didactic +
# expository story rubrics are unchanged from r6 (the new criteria are enacted-
# specific; the expository-classed forms get their signal from form overlays).
STORY_RUBRIC_R7 = dict(STORY_RUBRIC)
STORY_RUBRIC_R7["enacted"] = (
    'Score this WHOLE STORY against its plan. Reply ONLY JSON: {"ending_settled": '
    '0|1|2, "prot_changed": 0|1|2, "connected": 0|1|2, "theme_embodied": 0|1|2, '
    '"continuity": 0|1|2, "consequences_persist": 0|1|2|null, "prot_interior": '
    '0|1|2, "cast_distinct": 0|1|2|null, "named_on_page": 0|1|2, '
    '"evidence": {"<criterion>": "<short quote>", ...}}\n'
    "ending_settled: 2 = the central want is settled on the page, won or lost, and "
    "the premise's specific conflict is answered; 1 = half-settled or drifting "
    "close; 0 = deferred or atmospheric close.\n"
    "prot_changed: is the protagonist different at the end, shown not told?\n"
    "connected: judge CAUSAL ORDER, not shared topic — 2 = later pages DEPEND on "
    "earlier ones (your evidence must name a page that could not be moved without "
    "breaking what follows); 1 = a loose sequence, some consequences carried; 0 = "
    "the pages could be read in a different order with little loss — a shared "
    "setting or recurring vocabulary does NOT count.\n"
    "theme_embodied: how do the ideas arrive? 2 = enacted — things that HAPPEN to "
    "the protagonist carry them; 1 = mixed; 0 = recited (a sage or voice explains "
    "the ideas — a lecture wearing a story).\n"
    "continuity: 2 = nothing contradicts an established fact — names, roles, world "
    "details, and who-did-what hold across the whole story; 1 = one small slip; "
    "0 = a real contradiction (a character or fact changes with no cause — a name "
    "swaps mid-story, an established detail flips).\n"
    "consequences_persist (null if the plan lists no price): 2 = a price paid STAYS "
    "paid — a loss, wound, or cost from an earlier page still constrains later "
    "pages; 1 = partly carried then faded; 0 = a paid price is silently undone (the "
    "protagonist is whole again as if it never happened).\n"
    "prot_interior: 2 = the protagonist has inner life — wants, judgments, felt "
    "reactions drive the page; 1 = thin, mostly external; 0 = a camera with a name, "
    "no interiority (note: a deliberately opaque viewpoint that still conveys want "
    "through action can score 2 — judge whether the reader feels a mind, not word "
    "count of feelings).\n"
    "cast_distinct (null if there is no cast): 2 = the cast are distinguishable — "
    "different diction, concerns, or behavior, each their own person; 1 = blurry; "
    "0 = interchangeable, one voice wearing several names.\n"
    "named_on_page: 2 = the protagonist is called by NAME in the prose, early and "
    "across the story; 1 = named once or twice then pronouns; 0 = never named in "
    "the pages — judge the PAGES, not the plan header. (DIAGNOSTIC: a masterpiece "
    "may leave its viewpoint unnamed — reported, not scored.)")

# FORM OVERLAYS — 1-2 extra criteria per form, generated OFF the form's own
# FORMS[] contract text so the render instruction and the judge criterion share
# ONE source of truth: the overlay prompt injects FORMS[form] verbatim as "the
# form's contract" and asks whether the page-set honors these named checks. Only
# fires under r7, only for forms listed here. Criteria are 0-2, evidence-quoted.
FORM_OVERLAYS = {
    "epistolary": [
        ("correspondence_real",
         "each letter answers what the previous one actually said and adds its own "
         "matter — a real exchange, not two monologues sharing a page"),
        ("voices_distinct",
         "the correspondents are distinguishable — different diction, concerns, and "
         "stance, not one voice signing two names"),
    ],
    "case": [
        ("evidence_chain",
         "the decision points connect as evidence — each choice and outcome follows "
         "from the situation laid out, and the closing principle is earned by the "
         "specifics, not asserted"),
    ],
    "dialogue": [
        ("questions_do_work",
         "the questioning voice advances the inquiry — each question exposes grounds "
         "or turns an admission into a contradiction, and the position visibly "
         "changes (claim → objection → concession → sharper claim)"),
    ],
    "debate": [
        ("clash_real",
         "both sides argue a genuine tension at full strength and each rebuts the "
         "SUBSTANCE of the other's last turn — not caricatures, not parallel "
         "monologues that never touch"),
    ],
    "chronicle": [
        ("time_spine",
         "the entries hold a clear before-and-after order and each names what "
         "changed — sequence itself carries the meaning; nothing is scrambled or "
         "floats free of the timeline"),
    ],
    "interview": [
        ("answers_responsive",
         "each answer responds to the host's actual question with substance from the "
         "material, and the host's questions react to what was just said — a real "
         "exchange, not a questionnaire"),
    ],
}


def _forms_contract(form: str) -> str:
    """The form's own render-contract text (FORMS[form] in dwell.py) — the shared
    source of truth the overlay judges against. Best-effort import; empty on
    failure (the overlay then judges on its criterion descriptions alone)."""
    try:
        from dwell import FORMS
        return (FORMS.get(form) or "").strip()
    except Exception:
        return ""


def overlay_rubric(form: str) -> tuple[str, list[str]] | None:
    """(judge_system_prompt, criterion_names) for a form's overlay, or None."""
    crits = FORM_OVERLAYS.get(form)
    if not crits:
        return None
    contract = _forms_contract(form)
    fields = ", ".join(f'"{n}": 0|1|2' for n, _ in crits)
    body = "\n".join(f"{n}: {d}." for n, d in crits)
    sys_ = (f"Score how well this WHOLE {form.upper()} honors its form. Reply ONLY "
            f'JSON: {{{fields}, "evidence": {{"<criterion>": "<short quote>", ...}}}}\n'
            + (f"The form's contract (what it promised to be):\n{contract}\n\n"
               if contract else "")
            + "Score each 0-2 (2 = fully honored, 1 = partially, 0 = the form is "
              "broken or faked):\n" + body)
    return sys_, [n for n, _ in crits]


def story_rubric_for(fclass: str, rubric: str) -> str:
    return (STORY_RUBRIC_R7 if rubric == "r7" else STORY_RUBRIC)[fclass]


def page_rubric_for(fclass: str, rubric: str) -> str:
    return PAGE_RUBRIC[fclass]        # r7 leaves page-level criteria unchanged


def layer3(s: dict, fclass: str, model: str = JUDGE_MODEL,
           rubric: str = DEFAULT_RUBRIC, form: str = "") -> dict:
    key = _api_key()
    if not key:
        return {"skipped": "no ANTHROPIC_API_KEY"}
    out: dict = {"model": model, "rubric": rubric, "pages": []}
    plan = "\n".join(f"{b['i']+1}. {b['event']}"
                     + (f" [price: {b['price']}]" if b["price"] else "")
                     + (f" [mood: {b['mood']}]" if b["mood"] else "")
                     for b in s["beats"])
    header = (f"PREMISE: {s['premise']}\nPROTAGONIST: {s['protagonist']}\n"
              f"CAST: {'; '.join(s['cast'])}\nPLAN:\n{plan}\n")
    tails = {p["n"]: p["body"].strip()[-350:] for p in s["pages"]}
    for p in s["pages"]:
        if not p["gate"]:
            continue                      # judge the keyframes; bridges ride L1/L2
        b = s["beats"][p["beat"]] if p["beat"] < len(s["beats"]) else None
        if not b:
            continue
        prev = tails.get(p["n"] - 1, "")
        user = (header + f"\nTHIS PAGE'S BEAT: {b['event']}"
                + (f"\nBEAT PRICE: {b['price']}" if b["price"] else "\nBEAT PRICE: none")
                + (f"\n\nPREVIOUS PAGE'S ENDING (for follows_prev):\n…{prev}"
                   if prev else "\n\n(no previous page — follows_prev: null)")
                + f"\n\nPAGE {p['n']} TEXT:\n{p['body'][:4200]}")
        try:
            j = _judge_call(key, page_rubric_for(fclass, rubric), user, model)
            if j is not None:
                j["page"] = p["n"]
                out["pages"].append(j)
        except Exception as e:
            out.setdefault("errors", []).append(f"p{p['n']}: {str(e)[:60]}")
    whole = header + "\n\nFULL TEXT:\n" + "\n\n".join(
        f"[page {p['n']}]\n{p['body']}" for p in s["pages"])[:44000]
    try:
        j = _judge_call(key, story_rubric_for(fclass, rubric), whole, model)
        if j is not None:
            out["story"] = j
        else:
            out.setdefault("errors", []).append("story: reply had no parseable JSON")
    except Exception as e:
        out.setdefault("errors", []).append(f"story: {str(e)[:60]}")
    # FORM OVERLAY (r7 only) — a third call scoring the form's own contract; its
    # criteria are quality (counted) unless in the diagnostic set. Merged into
    # out["story"] so the review UI / calibrate see one criteria bag per story.
    ov = overlay_rubric(form) if rubric == "r7" else None
    if ov and s["pages"]:
        sys_, ov_crits = ov
        try:
            j = _judge_call(key, sys_, whole, model)
            if j is not None:
                out["overlay"] = j
                st = out.setdefault("story", {})
                stev = st.setdefault("evidence", {}) if isinstance(
                    st.get("evidence"), dict) else None
                for c in ov_crits:
                    if isinstance(j.get(c), int) and not isinstance(j[c], bool):
                        st.setdefault(c, j[c])            # don't clobber a base field
                if stev and isinstance(j.get("evidence"), dict):
                    stev.update(j["evidence"])
        except Exception as e:
            out.setdefault("errors", []).append(f"overlay: {str(e)[:60]}")
    # aggregate 0-100 (rubric fields only — never the page number, never bools,
    # never a DIAGNOSTIC criterion). r6 has no diagnostics → byte-identical math.
    diag = DIAGNOSTIC_CRITERIA.get(rubric, set())
    nums: list[float] = []
    for pj in out["pages"]:
        nums += [min(v, 2) / 2 for k, v in pj.items()
                 if k not in ("page", "evidence") and k not in diag
                 and isinstance(v, int) and not isinstance(v, bool)]
    nums += [min(v, 2) / 2 for k, v in (out.get("story") or {}).items()
             if k != "evidence" and k not in diag
             and isinstance(v, int) and not isinstance(v, bool)]
    if nums:
        out["judge_score"] = round(100 * sum(nums) / len(nums))
    scored_diag = sorted(
        k for k in (out.get("story") or {})
        if k in diag and isinstance((out["story"]).get(k), int))
    if scored_diag:                        # transparency: what was asked-not-scored
        out["diagnostic_excluded"] = scored_diag
    seams = [pj["follows_prev"] for pj in out["pages"]
             if isinstance(pj.get("follows_prev"), int)]
    if seams:                              # the order-sensitive connection score
        out["seam_connected"] = round(sum(seams) / (2 * len(seams)), 2)
    return out


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("story")
    ap.add_argument("--vault", default="")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--judge-model", default=JUDGE_MODEL)
    ap.add_argument("--pv", default="")
    ap.add_argument("--form", default="")
    ap.add_argument("--rubric", default=DEFAULT_RUBRIC, choices=RUBRICS,
                    help="judge rubric version (default r6; r7 must not slide "
                         "under a running A/B — scores don't compare across versions)")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--history", default=str(HERE / "history.jsonl"))
    a = ap.parse_args()
    text = Path(a.story).read_text(encoding="utf-8")
    s = parse_story(text)
    form = a.form or s["form"] or \
        next((f for f in list(DIDACTIC) + ["case", "epistolary"]
              if f in Path(a.story).name), "story")
    fclass = form_class(form)
    brain = load_brain(a.vault) if a.vault else None
    vault_titles: set[str] = ({n.title for n in brain.nodes.values()}
                              if brain else set())
    row: dict = {"ts": time.strftime("%Y-%m-%d %H:%M"), "file": Path(a.story).name,
                 "pv": a.pv or s["pv"] or "?", "form": form, "fclass": fclass,
                 "rubric": a.rubric, "pages": len(s["pages"]),
                 "beats": len(s["beats"])}
    m = re.search(r"gen_(\d+)_d(\d+)", Path(a.story).name)
    if m:
        row["seed"] = int(m.group(1))
        g = m.group(2)          # str(dream) sans ".": "085"→0.85, "005"→0.05
        row["dream"] = 1.0 if g == "1" else (
            int(g[1:]) / 10 ** len(g[1:]) if g.startswith("0") and g[1:] else None)
    if not s["pages"]:
        # unparseable dialect (old header format) — never log a legit-looking
        # all-zeros row; the trend must hold only rows that measured something
        print(f"[no pages parsed from {Path(a.story).name} — old format? "
              f"not logged]", file=sys.stderr)
        a.no_log = True
    row["L1"] = layer1(s, fclass, vault_titles)
    if brain is not None:
        row["L2"] = layer2(s, brain)
    if a.judge:
        # a low-dream FACTUAL TOUR has no protagonist by design — judging it on
        # protagonist-change/premise criteria penalizes correct behavior (r2)
        jf = "expository" if (fclass == "enacted" and not s["protagonist"]) else fclass
        row["L3"] = layer3(s, jf, a.judge_model, a.rubric, form)
    if not a.no_log:            # persist FIRST — a print crash must not lose
        with open(a.history, "a", encoding="utf-8") as f:   # paid judge rows
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, indent=2, ensure_ascii=False))


def _utf8_stdout() -> None:
    # Windows cp1252 stdout chokes on U+2011/U+2192 etc. in evidence quotes
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


if __name__ == "__main__":
    _utf8_stdout()
    main()
