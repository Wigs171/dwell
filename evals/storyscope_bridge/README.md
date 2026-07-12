# StoryScope bridge — the humanlikeness fingerprint

Scores generated stories against [StoryScope](https://github.com/jenna-russell/storyscope)'s
human-vs-AI classifier (arXiv 2604.03136: 304 interpretable narrative features,
93% F1 from narrative structure alone) and appends one line per story to
`evals/humanlikeness.jsonl` — the data behind the review server's
`/humanlikeness` point graph.

## Setup

1. Clone StoryScope and fetch its released data (taxonomy + the
   `storyscope_features.parquet` training frame), then retrain the binary
   classifier with their stage-6 script (the released model files drifted from
   the released encoder; retraining takes minutes):

   ```
   git clone https://github.com/jenna-russell/storyscope
   # follow their README for data/; then their stage-6 train -> a binary model
   ```

2. Copy this directory into the checkout as `dwell_bridge/`:

   ```
   cp -r evals/storyscope_bridge <storyscope>/dwell_bridge
   ```

   (The scripts import `storyscope.utils` from their parent directory, and
   `hl_score.py` expects the retrained model at `dwell_bridge/models/binary/model.json`.)

3. Point the bridge at this repo's evals and wire the harness:

   ```
   export DWELL_EVALS=/path/to/dwell/evals
   export DWELL_FINGERPRINT_SCRIPT=<storyscope>/dwell_bridge/fingerprint.py
   export ANTHROPIC_API_KEY=...        # the pinned feature rater
   ```

## Use

```
python dwell_bridge/fingerprint.py evals/path_test_*.md   # ad hoc
python evals/battery.py --sweep 20 --judge --fingerprint  # post-sweep pass
python dwell_bridge/export_poles.py                       # once: HUMAN/AI poles for the graph
python dwell_bridge/diagnose.py --fresh-dir dwell_bridge/fp_features/<run>
```

Then open the review server (`python evals/review_server.py`) at
`/humanlikeness` for the live three-band point graph.

## The laws (learned the expensive way)

- **Joint-encode always.** StoryScope's categorical encoding is data-dependent:
  encoding a small batch alone scrambles category codes against the training
  frame and the classifier outputs constant garbage. `hl_score.py` always
  encodes jointly with the reference parquet and slices your rows off.
- **One pinned rater.** Features are extracted with a dated model snapshot
  (`claude-haiku-4-5-20251001`, ~10 calls ≈ $0.015/story). LLM-rated feature
  classifiers do not transfer across raters or drifting checkpoints — never mix
  extraction runs from different raters in one comparison.
- **Distribution, not story grades.** Boundary stories flip between raters;
  the corpus distribution (and the per-feature SHAP decomposition) is the
  signal. Never treat a single story's P(human) as a quality score.
