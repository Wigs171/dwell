# Plan: aspect-aware auto layout + image cycling (fixes vertical-image cropping)

**Status: ✅ DONE + validated 2026-06-21 (REVISED design — see below).**

> ⚠️ **Design correction.** The first cut (retained in the original plan below) made
> the multi-image layouts (`diagonal`/`mosaic`) **pin-only**. The user rightly called
> that a lazy workaround — it leaves those layouts as dead code that realistically
> never appear. The shipped design instead keeps every layout **AUTO** but makes
> selection **aspect-aware**: a layout (single OR multi-image) is chosen only when
> each of its frames gets an image whose orientation fits that slot, so nothing is
> ever force-cropped *and* no layout is dead. `bottom` stays pin-only for a real
> reason (the reader flagged readability issues with a bottom-anchored image at large
> fonts), not laziness.

**Shipped design (backend `dwell_server.py`):**
- `_img_class(im)` → orientation class by ratio (tall <0.62 · portrait <0.9 · square
  <1.15 · landscape <2.0 · wide ≥2.0).
- `_SINGLE_OPTS` (class → eligible single layouts, first=primary, rest=variety on an
  image's repeat appearances): tall→`rail`, portrait→`side`/`magazine`, square→`side`/
  `inset`, landscape/wide→`top`.
- `_MULTI_SLOTS` (composition → ordered slot accept-sets, richest first): `mosaic`
  [{wide},{landscape,square},{square}], `diagonal` [{landscape},{square}]. Slots map
  to the reader's figure order; frames only ever crop WITHIN an orientation.
- `_match_slots(slots, classes, uses)` — **backtracking** assignment of distinct
  class-compatible images to slots (prefers least-shown); returns None if the pool
  can't fill every slot (→ that composition is skipped).
- `_build_image_schedule(classes)` — deterministic, repeating sequence of
  `(layout, [pool indices])`: alternates single/multi pages, every image appears ≥1×
  per cycle, multi only where it fits. Pure (least-shown + index tiebreak) so the same
  page ordinal always maps to the same composition (no caching).
- `_node_page_pos` (cursor, `node_page_order` on `DwellSession`) indexes the schedule;
  `_page_images(s,node,plan)` walks it (an explicit frontmatter pin still overrides).
- `_node_images` pool cap raised 3→48. Reader UNCHANGED this round (it already renders
  all 8 layouts; the natural-aspect single-image frames from the first cut stay).

**Validation:**
- **Schedule invariants** (synthetic + real pools, asserted): *2 portraits → `side`/
  `side`, NEVER diagonal/mosaic* (the reported bug); landscape+2portraits → no multi
  (no square for fig-b); keplers (landscape+square) → `top`+`diagonal` that fits;
  music (wide+landscape+square) → `top`+`mosaic` that fits; 8-mixed → all 8 covered,
  every multi slot-compatible. No composition ever places a class outside its slot.
- **Live browser** (restarted server, real reader): keplers auto-renders
  `top(monochord)` then **`diagonal`** with monochord(landscape→fig-a 1.33) +
  bells(square→fig-b 1.03) — both `bothLandscape_or_bothPortraitish`, no
  cross-orientation forcing, card fits. pythagoras (portrait 0.75) → `side` at natural
  ratio (no crop, width-anchored). `npm run check` + `py_compile` clean, no console
  errors.

Original (superseded) execution plan retained below for reference.

---

## The bug (user report, 2026-06-20)
A node with **2+ images** auto-selects a **multi-image layout** (`diagonal` for 2, `mosaic` for 3),
whose figure frames have **fixed aspect ratios** (diagonal: `fig-a` 3/2, `fig-b` 1/1; mosaic banner
21/6, etc.). So **portrait/vertical images get `object-fit: cover`-cropped into horizontal frames**
("forced into a horizontal aspect ratio"). User confirmed: removing one image → falls back to a
single-image layout (side/rail) → vertical image renders fine. *"if you put 2 vertical images
together it picks that style and breaks."*

Two more issues the user named:
- **Dwelling repeats the same image(s)** across successive pages on the same node.
- A node with **8 images** should **cycle logically** through them across pages — page 1 → image A,
  the next page on that node → image B, … wrapping around — not show "a few" repeatedly.

## The fix (design)
1. **Auto = ONE image per page.** Never auto-pick a multi-image layout. `diagonal`/`mosaic` (and a
   multi-image `magazine`) remain available **only when an author explicitly pins** `layout:` in
   frontmatter with chosen images (deliberate composition). Auto never composes multi-image.
2. **Layout chosen by THAT image's aspect ratio** (orientation always matches → no forcing):
   - wide (r ≳ 1.6) → `top` (alternate `bottom` for variety)
   - landscape (1.15–1.6) → `top`
   - square (0.9–1.15) → `side` (or `inset`)
   - portrait (0.62–0.9) → `side`
   - tall portrait (< 0.62) → `rail`
   (`magazine` single centered-column image is also a good portrait option — optional in rotation.)
3. **Cycle the node's images across pages.** The i-th DISTINCT page of a node uses `images[i % N]`;
   re-visiting the same page (same plan key) is stable (same image). Over enough pages, all N images
   appear, then wrap.
4. **Never crop vertical into horizontal** — preferred fix: give the auto figure frame the image's
   **natural aspect ratio** (the backend already sends `w`/`h`), capped by a max-height, so a vertical
   image stays vertical. Placement (`side` float / `rail` band / `top` block) still comes from the
   aspect→layout map; the frame's *shape* = the image's real shape.

## Execution — backend (`prototypes/dwell_server.py`)
Image selection lives entirely here (the reader just renders what it's told), so most work is backend.

- **`DwellSession`**: add `node_page_order: dict[str, list[str]] = field(default_factory=dict)`
  (node_id → plan-keys in first-seen order). Cleared on `action == "first"` like the other per-thread
  state in `_produce_page`.
- **`_node_image_index(s, node_id, plan_key, n) -> int`** (new): 
  ```python
  order = s.node_page_order.setdefault(node_id, [])
  if plan_key not in order: order.append(plan_key)
  return order.index(plan_key) % n
  ```
  Stable per (node, page); advances across distinct pages of the node. (Prefetch doesn't build the
  done payload, so it doesn't perturb this; coast/repage reuse the same plan_key → same index.)
- **Split `_choose_layout`**: keep a `_layout_for_image(img) -> str` (single-image, aspect-based map
  from §2, optionally alternating top/bottom & side/rail using `len(order)` parity for anti-monotony).
  Remove the `len>=3 → mosaic` / `==2 → diagonal` AUTO branches.
- **Rewrite `_page_images(s, node, plan)`** (now takes the plan/key so it can pick + stay stable):
  - resolve `images, pinned = _node_images(s, node)` (unchanged).
  - if `pinned` is a **multi-image** layout (`diagonal`/`mosaic`, or `magazine` when ≥1) → honor it:
    return ALL images + the pinned layout (author-composed; leave as today).
  - else **auto**: `idx = _node_image_index(s, node.id, plan.key(), len(images))`; `pick = images[idx]`;
    `layout = pinned if pinned in single-image set else _layout_for_image(pick)`; return **`[pick]`** +
    layout. (One image, aspect-matched, cycling.)
  - keep the served-URL shape identical (`/asset?session=…&path=…`, caption, attribution, w, h, aspect).
- **Call sites**: `_produce_page` and `_reproduce_page` already have `plan` + `key` in scope — change
  `**_page_images(s, node)` → `**_page_images(s, node, plan)`.
- Drop `diagonal`/`mosaic` from the AUTO path only; keep them in `_SUPPORTED_LAYOUTS` (pins still work).

## Execution — frontend (only if doing the natural-aspect frame, §2.4)
- `Reader.svelte`: for the auto single-image layouts, set the figure frame to the image's natural
  ratio: `<div class="figframe" style="aspect-ratio:{im.w}/{im.h}">` and DON'T apply the fixed
  per-layout `aspect-ratio` (override it, or gate the fixed rules to pinned multi-image only). Keep a
  `max-height` cap (e.g. `side`/`top` frames) so a very tall image can't blow the page; `domFitImage`
  already measures variable heights, so the fit still works. `rail` stays full-height (it's the
  tall-image home). Verify zoom-1 still fits and nothing clips.
- If natural-aspect is deferred: at minimum the aspect→layout map already routes portraits to
  `side`/`rail` (portrait-ish frames) and wides to `top`, which mostly avoids the forcing — the
  multi-image AUTO removal alone fixes the reported break.

## Curator
No change — it pins the image POOL on a node; cycling/selection is a reader-time concern over that pool.

## Open decisions (resolve while building)
- Natural-aspect frames (best, recommended) vs keep fixed orientation-appropriate frames (simpler).
- Anti-monotony: alternate top↔bottom and side↔rail by page parity? (nice-to-have)
- Is `magazine` (centered column image) in the portrait rotation, or pin-only?
- Cycling is per-session (resets when the session restarts) — fine; do NOT persist.

## Validation (live, against a seeded node)
- Seed a Pythagoras/MPH node with **3–4 mixed-aspect images** (≥2 portrait) in `images:` frontmatter
  (or pin via the curator). Restart `dwell-server`.
- In the reader: `beginAt(node)` → confirm **one** image, aspect-matched layout, **no crop** on the
  vertical one. Dwell forward several pages on the SAME node (branches that stay on the node) → confirm
  the image **advances** (image[0], image[1], …) and **wraps** after N; revisiting a page shows the
  same image (stable).
- Confirm a node with a **pinned `layout: diagonal` + 2 images** still renders diagonal (pins respected).
- `npm run check` clean; no console errors; `preview_eval` geometry (figframe aspect == natural w/h;
  card overflow ≤1).

## Pointers
Reader image wiring + layouts: see [reference_image_sourcing](memory) / `DWELL_HANDOFF.md`.
Backend image fns today: `_node_images`, `_choose_layout`, `_page_images`, `_aspect`, `_SUPPORTED_LAYOUTS`
in `dwell_server.py`. Reader layouts: `Reader.svelte` (`:global(.l-*)` CSS) + `layouts.css` (lab).
