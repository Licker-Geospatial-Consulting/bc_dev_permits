# QA & Re-harvest Notes

Operational notes recorded after QA'ing each municipality: how data is fetched, what decisions
were made, what went wrong, and what is still missing. Read this before re-fetching new permits -
it captures the non-obvious gotchas that are easy to re-hit.

`docs/COUNCIL.md` is the source-of-truth registry (endpoints, selectors, methods). This file is the
"what we learned the hard way" companion.

---

## Cross-cutting rules (apply to every municipality)

- **Always harvest with `--limit 0`.** The `dataset` CLI `--limit` defaults to **3** and silently
  truncates the plan. (`python -m bc_dev_permits.dataset --municipality <muni> --out json --limit 0`.)
- **PDF fields are read by Claude in-session**, not Ollama and not the Claude API (the Victoria
  workflow). The document-selection + merge/provenance code is shared (`harvesters/pdf_enrich.py`);
  the actual extraction is Claude reading the rendered PDF. Results are stamped
  `extraction_method="html+claude_read"`, per-field `pdf_claude`, `needs_review=True`.
- **Precision-first.** Emit a value only when unambiguous, else `NULL`. Deterministic HTML/table
  values take precedence over model/Claude reads; a genuine HTML-vs-PDF disagreement is **logged to
  `row['conflicts']`**, never silently dropped. (This conflict flag has caught real extraction
  errors - e.g. a West Van area-schedule misread.)
- **The `tools/<muni>_enrich.py` scripts are manual post-processing, not part of `make data`.** They
  rewrite `data/processed/<muni>.json` in place with the hand-read fields. A re-harvest **overwrites
  that JSON and wipes the enrichment**, so after every re-harvest you must re-run the enrich script:
  ```
  python -m bc_dev_permits.dataset --municipality <muni> --out json --limit 0
  python tools/<muni>_enrich.py
  ```
  The scripts are idempotent (a guard stops the `raw_text` notes from being appended twice), so
  re-running is safe.
- **New permits won't be in the enrich dicts.** `CLAUDE_READ` / `PARKING` / `ARCHITECTURAL` are keyed
  by `permit_id`. After a re-fetch, newly-appeared low-signal rows need **fresh in-session reads**;
  the old entries only re-apply to permits that still exist.
- **PDF URLs come from the row's own document list, never guessed.** Guessing West Van document URLs
  wasted time (3 wrong guesses). Pull the exact URL from the harvested `documents` for each row.
- **Render the project-DATA/statistics sheet, read it as an image.** Tabular data (FAR, unit mix,
  parking, lot area) is fragmented in the text layer; render the page with pymupdf
  (`get_pixmap(dpi=110).save(png)`) and read the PNG. Extract **every** attainable attribute, not a
  couple. Use building height as a storey proxy (~3 m/storey); sum lot totals for subdivisions.
- **Gather fields across EVERY PDF and page, then merge onto the one row.** Different documents carry
  different fields: a site plan gives floor area but no height, the elevations give height + parking,
  and a separate "Project Stats / Site Stats / Development Data" summary sheet gives the civic
  address, FAR and zoning. Don't stop at the first usable PDF - read the whole multi-sheet set
  (cover, site-stats, site plan, elevations) and combine. (Langley DP101531: the site plan has the
  floor area, the elevations have the height/parking, and only the DP.002 Site Stats sheet has the
  address, FAR and zoning.)
- **A missing address comes from the PDF.** When the HTML/list has no civic address, read it from the
  PDF's Project Summary / Site Stats / Development Data table, then the site plan, then the
  elevations (e.g. Langley DP101531 -> "8500 200th Street" on DP.002). `apply_claude_pdf` fills the
  null `address` like any other field.

---

## west_van - District of West Vancouver  (html + Claude-read PDF)

**How fetched.** Three published streams deduped by detail URL: Active (all) + Preliminary (all) +
Approved (**last 2 years only**, `RECENCY_YEARS`). Approved cutoff uses `application_date` = the
first "Month DD, YYYY" in the lead prose at the top of each detail page; undated pages are kept.
Whole site is behind **Cloudflare**, fetched via headless Chromium (Playwright) with a cool-down.
As of this session: **100 rows** (19 active, 16 preliminary, 65 approved of 177; 112 skipped as old).

**QA decisions / updates.**
- Enriched via `tools/westvan_enrich.py` in batches of in-session PDF reads (coach houses,
  subdivisions, single-family, institutional, commercial). Ended at **95/100 classed, 62 claude_read** (snapshot - counts shift on every re-harvest).
- `OVERRIDE_HTML` clears a field before merge when the HTML regex misfired, e.g. **DP22-085**: HTML
  read `0.11 m` (the requested height *variance overage*) as the building height; cleared it so the
  PDF's `6.40 m` fills.
- **6408 Bay St**: "four-storey building with a partial 5th floor" - kept HTML `4` storeys over the
  project-profile's `5` (the partial floor is rooftop amenity); detail kept in the note.
- **Retaining-wall / foreshore EDPs** (2475 Palmerston, 2650 Bellevue): classified `residential`
  (existing SF site) with **no units** - the permit builds a wall, not a dwelling.
- **1043 Millstream** (a TELUS cell-tower upgrade) and **cypress-village-area** (an area/master plan;
  the only readable page was a zoning-bylaw excerpt): left **unclassed (NULL)** - neither fits the
  five development classes.

**What went wrong / didn't work.**
- **"6 units" misread**: read the Creek area-schedule "Grand total: 6" as 6 dwelling units; it was 6
  floor-area line items (3 blocks × 2 floors). The conflict flag (HTML said 4) caught it - removed
  `units_total` from that entry. Lesson: a "total" on an area schedule is not a unit count.
- **Guessed PDF URLs** 3× before switching to the real URLs from each row's `documents`.
- **3 PDFs are server-blocked (HTTP 403)** on both plain and Referer requests: **2237 Palmerston,
  2265 Ottawa, 5665 Daffodil**. Every other West Van PDF fetched fine. Still unenriched.
- Note-duplication bug (notes re-appended each run) was found and fixed with an idempotency guard.

**Still missing.** 5 low-signal rows: `1043-millstream` (cell tower), `cypress-village-area` (area
plan), and the 3 403-blocked PDFs above.

**Re-fetch checklist.**
1. Cloudflare throttles long batches - fetch in **rounds** (`_browser_fetch` stops after
   `_MAX_CONSECUTIVE_FAILS`, disk cache persists), repeat until all pages are cached, then a final
   `--limit 0` run parses from cache (fast, no network).
2. The **approved window slides** (last 2 years): old approved drop out, new ones appear. Expect the
   row set and `is_parsed` counts to change.
3. Re-run `tools/westvan_enrich.py`; read any new low-signal rows in-session and add them to
   `CLAUDE_READ` (+ `BREAKDOWNS` for multi-building detail). Retry the 3 403 PDFs - they may unblock.

---

## surrey - City of Surrey  (PDF-first, Claude-read)

**How fetched.** Surrey publishes planning-report PDFs; fields are mined **deterministically from the
PDF text layer** (precision-first), scanning the **whole PDF**, not just page 1. Two parsers:
`pdf_parking` ("Number of Stalls" + both "Parking (Part 5)" layouts + component sum) and
`pdf_data_fields` / `_apply_pdf_data_table` (the single-column "Proposal"/"Proposed" summary table →
FAR, storeys/height, lot coverage, floor-area-by-use, unit mix, units, lot/parcel).

**QA decisions / updates.** The user QA'd Surrey and asked to read the full-PDF tables (floor area,
FAR, unit mix, units, parking), since data is often buried on later pages.
- The data-sheet parser **resurrected ~650 "memo-cover" reports** whose page-1 PROPOSAL never parsed:
  `is_parsed` 656 → 1309 → **~1415** (snapshot - shifts on re-harvest).
- Parsing rules that matter: label-line-then-value-line pairing by order; **unit mix trusted only if
  it sums to the stated Total**; **richest contiguous page-run only** (keeps the RM-zone comparison
  table out); FAR **decimal-comma** handled; data sheet overrides weaker PROPOSAL prose and logs
  conflicts. Lot/parcel fields (site area → `lot_area`, number of lots, density) read from the data
  sheet; subdivisions classified from the data-sheet page uses.
- `tools/surrey_enrich.py` adds the Claude-read layer, **targeted at low-confidence rows (score 0 /
  0.2)** only, not all 1400+: `PARKING` (17 reports whose stall table the text layer fragments) and
  `ARCHITECTURAL` (Format-C "PROJECT DATA" titleblocks + a DP-renewal whose table sits under the
  bundled original). Low-confidence count went **183 → 84**.
- **Format-C detector investigation**: rigorously found only 1 text-parseable titleblock (7922-0306)
  + 1 image-only (7926-0180). Recommendation stands: **do not build a general Format-C detector** -
  too rare; hand-read the few.

**What went wrong / didn't work (all fixed in the text parser).**
- FAR `0,46` (decimal comma) → `46`; fixed with `[.,]` + comma→dot + a `0<far<25` guard.
- Spaced thousands `12, 061` → `12`; fixed `_table_num` to `\d[\d,\s]*` + strip.
- Multi-site `Site 1: 9,744 / Site 2: 13,961` → office `1.0`; fixed `_area_value` to anchor the number
  before an area unit and decline when several.
- `m2` (not `m²`) not matched; broadened `_AREA_RE`.
- The 84 remaining low-confidence were categorized: **42** inter-office memos (no table), **36**
  non-building apps (liquor licences, non-farm use, stormwater), **3** image-only drawing sheets,
  **3** bundled (table under another app's id), **0** with their own parseable table missed.

**Still missing.** ~167 genuinely-unparsed memos; **FAR recall is modest (~221)** because FAR usually
sits in the RM-zone *comparison* table, which is deliberately excluded - reading its Proposed column
positionally (like parking) would add more. ~5 architectural key-value blocks (layout C, e.g. 1821
Southmere) have no dedicated key-value parser (too few to justify one).

**Re-fetch checklist.**
1. **Surrey PDF cache TTL is 6 hours.** Before a re-harvest, **touch the cached `surrey_*.pdf`
   mtimes**, or the harvest re-fetches all ~1469 PDFs over HTTP and some fail.
2. Re-run `tools/surrey_enrich.py` after the harvest. Low-confidence rows are the ones to review;
   read any new score-0/0.2 rows in-session and add to `PARKING` / `ARCHITECTURAL`.
3. Geocoding uses `surrey_query` (drops the `" - "` after the civic number, e.g.
   `12042 - 100A Avenue` → `12042 100A Avenue`).

---

## ubc - UBC Campus + Community Planning  (html + Claude-read PDF)

**How fetched.** Project cards from the planning.ubc.ca list, **paginated** (`?page=N`,
`MAX_PAGES=40`, `_all_project_cards`). The list was fixed this session from **10 → 149 projects**:
match `text.startswith("Image")` instead of requiring a DP number, and the status regex was broadened
(Complete / Withdrawn). Every row carries `documents` (planning.ubc.ca PDFs).

**QA decisions / updates.**
- `tools/ubc_enrich.py` adds in-session reads of each project's cover / project-data sheet. Ended at
  **104/149 classed, 16 claude_read**.
- Residences → `residential` with full unit mix (Gage South 362 units/651 beds; Totem Park Infill 354;
  Wesbrook Lot 3). Academic / recreation / museum → `institutional` (Biological Sciences, Wayne &
  William White, LIFE Building, Hebb, MOA Great Hall, Rec Centre North, Rugby Pavilion). **UBC Pump
  House → `industrial`** (Group F utility/pump station - the one non-institutional building).
- **Conflicts resolved by dropping redundant fields.** 6 rows initially flagged floor-area/storey
  disagreements where the UBC HTML already carried a *close* value (e.g. gross 26,940 vs PDF 27,263).
  Since deterministic HTML takes precedence and mine weren't correcting an error, I **dropped those
  structured fields** (keeping the precise figures in the NOTES) so the gap-fills (units, parking,
  FSR, lot) stand without noise. Final: **0 conflicts**.
- **Non-building projects left NULL**: transportation/parking plans, the Metro Vancouver *Regional
  Context Statement*, public-art relocations, sculptures, antennas, wayfinding, field/court and
  temporary-installation projects - most are image-only or carry no project-data sheet, and several
  aren't buildings that fit the five classes.

**What went wrong / didn't work.**
- **Geocoding matched off-campus street segments.** NW Marine Drive, University Boulevard and Wesbrook
  Mall all continue **east off campus**, so a plain `", Vancouver"` Nominatim query put `6303 NW
  Marine Drive` and `6088 University Blvd` outside the campus, and interpolated `3055 Wesbrook Mall`
  to the wrong (north) end. **Fix** (in `geocode.py`): UBC queries pass a **`viewbox` of the campus
  bbox with `bounded=1`**, and each result is validated against the campus **boundary polygon**
  (~200 m buffer); anything still outside falls back to the campus centroid. Result: **42 precise +
  107 centroid, 0 points outside the boundary**.
- `ubc_enrich.py` had the same note-duplication bug - fixed with the idempotency guard.

**Still missing.** ~45 rows unclassed - the landscape / transportation / public-art / study projects
above (no project-data sheet to read). The 107 centroid-placed rows are projects whose only "address"
is a name or a described location (no civic address to geocode) - they stack at campus centre by
design.

**Re-fetch checklist.**
1. Run with `--limit 0`; confirm pagination still returns ~149 (watch for the planning.ubc.ca card
   markup changing - the `startswith("Image")` match is brittle).
2. Re-run `tools/ubc_enrich.py`; read new low-signal building projects in-session.
3. Re-run the geocode (UBC uses the bounded-viewbox + polygon-validation path automatically).

---

## Other municipalities (no PDF-read QA pass this session)

These were harvested by their listed methods (see `docs/COUNCIL.md` for endpoints/selectors). No
session-specific Claude-read QA decisions were recorded for them here; the cross-cutting rules apply.

- **north_van** (html) - detail pages. Note: an Ollama enrichment run was interrupted in an earlier
  session; the project standard is Claude-read PDFs, not Ollama.
- **victoria** (prospero / "OurCity" ASP.NET tracker) - the original Claude-read PDF workflow all the
  other munis follow came from Victoria.
- **coquitlam**, **port_moody**, **new_west** (arcgis webmaps; Port Moody + New West also cross-ref an
  engagement site) - fields come from map feature attributes.
- **maple_ridge** (VertiGIS Studio webmap).
- **halton_hills** (Ontario; arcgis + html) - the only non-BC source; geocodes in Ontario.

When you do a PDF-read QA pass on any of these, add a full section here following the West Van /
Surrey / UBC template above.

---

## Dashboard (tools/dashboard/)

The map dashboard is a separate build; re-running it after a re-harvest is `build_data.py` →
`build_html.py`. Gotchas learned:

- **Adding a municipality needs THREE edits, not one.** The province→muni map `PROVINCES` in
  `dashboard.template.html` is **hardcoded**; a muni missing from it is **filtered out of the map
  even when its rows are in `permits.json`** (this happened to Surrey). To add a muni: (1) `FILES` in
  `build_data.py`, (2) the `PROVINCES` list in the template, (3) a boundary + geocoding entry.
- **Boundaries** (`fetch_boundaries.py`) come from OSM Nominatim and the script **regenerates the
  whole file**. Nominatim is non-deterministic - one run returned **Halton Hills as a Point** instead
  of a polygon. So when adding one muni, **splice just its feature** into `boundaries.json` rather
  than overwriting all of them.
- **Geocoding** (`geocode.py`) has per-slug query helpers (`surrey_query`, `wv_query`, `ubc_query`).
  UBC additionally needs the campus **viewbox+bounded** search and **point-in-polygon validation**
  (see the UBC section) or its dots land off-campus.
- Rows already carrying `latitude`/`longitude` (arcgis/webmap sources) are used directly; only the
  coordless HTML/PDF sources are geocoded, cached in `geocache.json` (OSM rate limit 1.1 s).
- The dashboard is also published as a password-protected Cloudflare Pages site - see
  `permit-atlas/README.md` (uses a root `_worker.js`, because dashboard Direct Upload does **not**
  compile a `functions/` directory).
