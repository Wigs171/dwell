# Assisted Image Sourcing — research & v1 design

Consolidated from three research passes (2026-06-20), live-verified against the APIs.
This is the reference for building the **assisted image-sourcing tool**: given a vault node
(title + entities + page text), suggest freely-usable images for one-click human approval,
then download them with full license/attribution into `raw/assets/<node-slug>/`.

Model: **auto + pins** — the tool suggests; a human approves; an approval becomes a durable
frontmatter `image_pin` that future runs skip.

**Audience (decided 2026-06-20): admin / vault-authoring only — NOT user-facing.** Readers never
see curation; it's a separate tool for whoever builds the vault (a CLI / admin interface), kept out
of the Dwell reading app. The reader only ever consumes the resulting pins.

---

## 1. v1 sources (start with these three)

The trio gives precision + breadth + clean art quality with minimal license risk:

1. **Wikimedia Commons + Wikidata — the spine (always on).** The *only* source with a
   **structured entity bridge** for high-precision matching: resolve topic → Wikidata QID, then
   - **P18** (the entity's canonical image) — `wbgetentities?ids=QID&props=claims` → `claims.P18[0].mainsnak.datavalue.value` (a bare Commons filename).
   - **`depicts` search** — Commons `generator=search&gsrsearch=haswbstatement:P180=<QID>&gsrnamespace=6` returns every file *tagged as depicting* the entity. Highest precision.
   - **keyword** — `generator=search&gsrsearch=<title>&gsrnamespace=6` as recall backstop.
   - **category** — Wikidata `P373` → Commons `list=categorymembers` as breadth fallback.
   Hydrate any file via `prop=imageinfo&iiprop=url|size|mime|extmetadata&iiurlwidth=N` (batch up to 50 titles/call). License/attribution come from `extmetadata` (§4).
2. **Openverse — recall layer.** One keyless API (`https://api.openverse.org/v1/images/`) over
   ~800M CC/PD images from ~80 providers, with **machine-readable license filtering** (`?license=cc0,pdm` or `license_type=all-cc`) and a **pre-built `attribution` string** per result. Best first-pass breadth for non-art / modern / technical nodes. Anonymous = 1 req/s, `page_size≤20`; register (`POST /v1/register`) for higher limits. NB: it already indexes Commons → dedupe against the spine.
3. **The Met — CC0 art quality.** `https://collectionapi.metmuseum.org/public/collection/v1/` —
   keyless, every Open Access object is **CC0**, `isPublicDomain` boolean, direct `primaryImage` URLs (no IIIF needed). Best for humanities/classical/Renaissance art the other two cover patchily. Static dataset → cache locally.

**Deferred to v2 (drop-in adapters behind the same normalize interface):** Cleveland (cleanest
`share_license_status` field), Smithsonian (CC0, ~3M, needs api.data.gov key — great for portraits/nathist/instruments), AIC (CC0, single-call filterable), Europeana (`reusability=open`, best per-item rights URIs), Wellcome, Internet Archive, Flickr/British Library.

## 2. Fuller catalog (by need)

| Source | Access | License | Best for |
|---|---|---|---|
| **Wellcome Collection** | keyless; `…/catalogue/v2/works?query=…&items.locations.license=cc-0`; IIIF | clean per-item | ⭐ **esoteric/alchemical/astrological + history of medicine/science** (MPH/occult vaults) |
| Internet Archive | keyless; `advancedsearch.php?q=mediatype:texts AND subject:…&output=json` | mixed (filter `licenseurl`/`possible-copyright-status`) | **old book engravings, frontispieces, alchemical treatises** |
| Flickr → **British Library** | Flickr API (`is_commons=true`, license 7/9/10); BL account `12403504@N02` | PD / no-known-restrictions | **~1.07M old-book illustrations, maps, decorative initials** |
| Smithsonian OA | `api.si.edu` + api.data.gov key | CC0 | portraits (NPG), natural history, science instruments |
| AIC / Cleveland | keyless, single-call PD filter, IIIF | CC0 | high-res art history / works-on-paper |
| Europeana | key; `reusability=open` | rights URIs | European art, manuscripts, maps |
| Library of Congress | `…?fo=json` | mixed | ⭐ **maps**, prints & photographs |
| BHL | key; `api3?op=PublicationSearch` | PD/CC | natural-history illustration |
| NASA | keyless `images-api.nasa.gov` | PD (policy) | astronomy / space history |

**⚠️ Status (2026):** Rijksmuseum classic `?key=` API **shut down Jan 2026** (now keyless LOD+IIIF
at `data.rijksmuseum.nl`); **NYPL API retiring Aug 1 2026** (don't build on it); British Library's
own platform impaired since the 2023 ransomware attack → **use the Flickr mirror**; **Getty has no
keyword-search API** (SPARQL/LOD + IIIF only). Public Domain Review / PDIA = curation, **no API**.

---

## 3. Matching strategy

**Two retrieval paths, kept distinct (don't blend their scores):**
- **entity-bridge** (QID → P18 / `depicts`) — high precision. Use when an entity resolves to a
  confident QID naming a concrete depictable thing.
- **keyword / museum-subject** — high recall, noisy. Always run 1–2 as a backstop.

Carry a `mode` on every candidate; the ranker adds a provenance boost so structured gold isn't
buried by noisy keyword hits with incidentally-high similarity.

**QID resolution** (`wbsearchentities`) must **disambiguate with page context** — embed each
candidate's `label + description` and pick the best cosine to the node title/text (kills
"Mercury planet vs god vs element"); require a min similarity + a `P31` type sanity check.

**Abstract topics** ("music of the spheres", "the World Soul") cascade:
1. try the node's own QID (many abstractions have one: Musica universalis = Q1411) → its P18/depicts;
2. **bridge to concrete related entities already in the node's wikilinks** (Pythagoras, Robert Fludd, Kepler/*Harmonices Mundi*, monochord) — the graph supplies depictable anchors;
3. motif/representative-artwork keyword search (prefer historical art over modern stock);
4. diagrams as last resort (often the most pedagogically useful).
Always show the human a **"why"** ("suggested via: Robert Fludd → cosmic monochord").

**Ranking** (embedding core + structured boosts − quality penalties + diversity):
```
score = 0.55*sim(page) + 0.15*sim(title)
      + 0.25 if candidate.depicts ∩ node.QIDs        # strongest structured signal
      + 0.15 if candidate is an entity's P18
      + provenance boost (bridge .10 / museum .05 / keyword 0)
      + caption-entity match (≤.10)
      − quality penalty (reject < 640px; graduated to ~1200px; odd MIME/aspect)
      − generic penalty (stock/icon/logo/"flag of"; wrong-person portrait guard)
```
Then **MMR diversity** (mix a portrait + an artwork + a diagram) → surface **top 6–10**.

**Fan-out budget per node:** bridge top-3 entities by salience; ~4–7 queries; 2–3 sources;
cap ~60 candidates pre-rank → ~40 post-license → top 6–10 shown.

---

## 4. License — a HARD pre-rank gate (not a score)

The vault may be redistributed, so commercial-use AND derivative permissions must both be present.
Filter *before* embedding; junk never reaches the human.

| License family | Policy | Obligation |
|---|---|---|
| Public Domain (PD-old/PD-art/expired) | **ALLOW** | none (courtesy credit) |
| CC0 1.0 | **ALLOW** | none |
| CC BY | **ALLOW** | attribute (TASL) |
| CC BY-SA | **ALLOW (with care)** | attribute **+ share-alike**: store the image **byte-unmodified** so it's mere aggregation, not a derivative; SA does **not** infect your prose |
| CC *-NC / *-ND | **AVOID** | (NC kills redistribution; ND kills crop/resize) |
| All-rights-reserved / unknown / template-only | **AVOID** (→ review) | absence of a clear free license = not free |

**Commons classifier:** spine off `extmetadata.AttributionRequired` ("true"/"false" strings) +
pattern-match `LicenseShortName`/`License`. Pitfalls: **PD-Art trap** (a photo of a *3-D* PD
object carries the *photographer's* CC license — read the file's own license, never infer "it's old
so it's PD"); `Artist`/`Credit` contain **HTML** (strip); non-empty `Restrictions` (trademark/
personality) ⇒ never auto-approve. `Copyrighted:False` is a positive PD signal; `Copyrighted:True`
≠ avoid.

**Attribution = TASL** (Title, Author, Source-link to the *description* page, License + deed link),
stored as plain + Markdown. Openverse/Met ship usable attribution directly.

---

## 5. Rights schema + dedup + ops

**Unified per-image record** (serialized into the manifest + frontmatter pin):
```
{ source, source_page_url, image_url, iiif_base?, license_id, license_family, license_url,
  author, author_url, attribution_required, attribution_text, title, depicts_qids[],
  width, height, sha256, phash, policy_status }
```

**De-dup across sources** (same artwork on Met + Commons + Europeana), cheap→expensive:
sha256 (exact bytes) → shared QID / museum accession → normalized source URL → **pHash** (Hamming
≤6–8, the workhorse, run on the thumbnails already fetched) → metadata near-match. Keep the
most-permissive + highest-res representative; record dropped members in `also_at`.

**Etiquette/ops:**
- **Descriptive `User-Agent` on every request** (WMF blocks generic UAs → 429): `CompendiumImageSourcer/1.0 (<url>; you@example.com)`. This is what fixed the earlier 429s.
- Wikimedia: ~1 req/s sustained, serialize per host, **batch `imageinfo` (≤50 titles)**, `&maxlag=5` on batches, honor `Retry-After`. Reuse `compendium/sources/fetcher.py` politeness.
- **IIIF** where available: `info.json` → `…/full/max/0/default.jpg` (respect `maxWidth/maxArea`).
- Cache API responses (license is stable; ~30-day TTL) + thumbnails; content-address assets by hash (idempotent re-runs). Cap stored longest-edge ~2048–3000px; keep `image_url` for a higher-res refetch.
- `Special:FilePath/<file>?width=N` is the stable fetch-by-filename redirect (rasterizes SVG→PNG, capped 4096px) — but it carries **no license metadata**, so always reconcile with an `imageinfo` call.

---

## 6. v1 architecture (flow)

```
node (frontmatter + page text)
 → build ImageBrief (entities + key phrases + node QID + 1-hop wikilink anchors)
 → query router → typed queries {entity-bridge | keyword | museum-subject}
 → fan out to 2–3 sources (Commons+Wikidata always; + Openverse OR Met by topic)
 → hydrate + normalize → unified rights schema (batched imageinfo / IIIF info.json)
 → LICENSE GATE (drop everything not in {PD, CC0, CC-BY, CC-BY-SA})   ← before ranking
 → de-dup (sha256 → QID/accession → URL → pHash → metadata)
 → embed candidate_text (existing sentence-transformer) + score + MMR diversity
 → present TOP 6–10 (thumbnail · license badge · attribution preview · "why")
 → human one-click approve / pin / reject / "show more"
 → download chosen (IIIF/largest) → store by hash in raw/assets/<node-slug>/
 → write manifest.json + per-image rights ; set frontmatter image_pin
```

**auto + pins frontmatter** (durable human decision; pinned nodes skipped on re-run):
```yaml
image_pin:
  file: raw/assets/world-soul/fludd-cosmic-monochord.jpg
  source_page_url: https://commons.wikimedia.org/wiki/File:RobertFludd_Monochord.jpg
  license_id: PD-old
  attribution: '"The divine monochord" by Robert Fludd (1617), via Wikimedia Commons (public domain)'
  layout: magazine        # optional layout hint for the reader
  pinned_by: human
```
A manual URL paste is also a "pin" — still run through normalize → license-gate → download →
attribution so even hand-picks get correct rights metadata.

---

## 7. Build order (proposed)

The tool is moot until the reader can *show* images, so:
1. **Reader image wiring first** (the "option 2" track): backend resolves a node's images
   (sources → `raw/assets/`) + `/asset` route + `images[]`/`layout` on `/page`; frontend routes
   pages through `PageLayout` + `fitLayout`; keep karaoke/clarify/narration intact. Seed a test
   node with the demo images to build against (no vault has images yet).
2. **Then this sourcing tool**, starting with the Commons+Wikidata spine (the entity bridge is the
   whole value), then Openverse + Met, then the review UI + pin writing.
