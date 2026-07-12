"""Export Dwell's eval corpus (story-form only) into StoryScope's expected
CSV shape: prompt_id, title, human_story (the column name only selects the
source tag in outputs — extraction itself is source-agnostic; we relabel at
inference time). Page bodies are joined; plan headers / GATE lines stripped.
"""
import csv, re, sys
from pathlib import Path

# where the dwell repo's evals live (humanlikeness.jsonl, stories/, poles):
# set DWELL_EVALS to your dwell checkout's evals/ directory.
import os as _os
EVALS = Path(_os.environ["DWELL_EVALS"]) if _os.environ.get("DWELL_EVALS")     else Path(__file__).resolve().parent.parent / "evals"
STORIES = EVALS / "stories"
OUT = Path(__file__).resolve().parent / "dwell_stories.csv"
PAGE_RE = re.compile(r"^## page (\d+) · (\w+) · (.+?) · (\d+)w\s*$", re.M)

rows = []
pid = 0
only = sys.argv[1:] if len(sys.argv) > 1 else None
for f in sorted(STORIES.glob("path_test_*.md")):
    n = f.name
    if "tutorial" in n or "control" in n or "_auth" in n:
        continue                      # fiction features → story form only
    if only and not any(o in n for o in only):
        continue
    text = f.read_text(encoding="utf-8", errors="replace")
    heads = list(PAGE_RE.finditer(text))
    if len(heads) < 4:
        continue
    bodies = []
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        b = text[h.end():end].split("\npages=")[0]
        b = re.sub(r"\n---\s*$", "", b)
        b = "\n".join(l for l in b.splitlines() if not l.startswith("GATE "))
        bodies.append(b.strip())
    story = "\n\n".join(bodies)
    if len(story.split()) < 1200:
        continue                      # too short for the 5k-word feature space
    pid += 1
    rows.append({"prompt_id": pid, "title": n[:-3], "human_story": story})

with open(OUT, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["prompt_id", "title", "human_story"])
    w.writeheader()
    w.writerows(rows)
print(f"exported {len(rows)} stories -> {OUT.name}")
for r in rows:
    print(f"  {r['prompt_id']:>3}  {r['title']}  ({len(r['human_story'].split())}w)")
