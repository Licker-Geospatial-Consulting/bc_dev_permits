---
name: dev-permit-scrapper
description: Scrape development permit and development application data from BC municipality websites into a normalized SQL table, covering commercial, residential, mixed-use, industrial, and institutional projects. Use this whenever the user wants to extract, harvest, or parse development permits, development applications, rezonings, or DVPs from a city/municipal/council website or webmap - including HTML application pages, ArcGIS or VertiGIS webmaps, and PDF documents. Also use when the user mentions a specific municipality by name (North Vancouver/CNV, Coquitlam, Maple Ridge, Port Moody, West Vancouver, New Westminster, UBC) in the context of building or development data, or asks to set up local PDF field extraction with Ollama. Prefer parsing fields directly from the page; only fall back to saving PDF links when the page has no relevant text with respect to fields that needs to be fetched.
---

# Development Permit Scraper Workflow

Use this workflow when harvesting a new source, adding a municipality, repairing or modifying an existing harvester, or changing extractions.

## Guiding rule

**Parse first, PDF-fallback second.** For every application:

1. Try to extract the schema fields deterministically from the page's own text (prose paragraphs,
   `<div>`s, tables, or a webmap feature's attributes).
2. If the page has **no relevant text with respect to fields that needs to be fetched**, do not fabricate it - leave it
   `NULL`. If the page has **no usable text at all** (e.g. it's just a stub with
   attachments), save the PDF/document links into the `dev_document` table and set
   `needs_pdf_extraction = true`. A later Ollama pass fills the fields from the PDFs.

This keeps cheap deterministic parsing on the common path and reserves the
expensive local-LLM PDF pass for pages that genuinely need it.

## Workflow

1. **Read the schema.** `references/schema.sql` is the target. Every source maps to
   these columns. Read it before writing any parser.
2. **Pick the source type** for the municipality from `docs/COUNCIL.md`, then open the
   matching reference file:
   - HTML application pages (North Van, West Van, UBC, New West detail pages) →
     `references/html_detail_pages.md`
   - ArcGIS webmaps (Coquitlam, Port Moody, New Westminster list) →
     `references/arcgis_webmaps.md`
   - VertiGIS Studio webmap (Maple Ridge) → `references/vertigis_webmaps.md`
   - Prospero / "OurCity" ASP.NET permit tracker (Victoria) →
     `references/prospero_tracker.md`
3. **Harvest** the list of applications, then **enrich** each one (parse fields or
   store PDF links per the rule above).
4. **Classify** `development_class` (commercial / residential / mixed / industrial /
   institutional) - this is a required output for every row.
5. **Extract prose → fields.** Parse fields that are already in the HTML/map content deterministically
   (`bc_dev_permits/features.py` - regex/selectors, no model call). Reserve Ollama (`bc_dev_permits/modeling/predict.py`,
   contract in `references/extraction_prompt.md`) for the PDF-fallback path only - reading fields out of
   PDF documents when a page had no usable text. See `references/ollama_pdf_extraction.md`.

   **PDF enrichment - Claude-read, in priority order (West Van, UBC).** For sources where the richer
   detail (parking, unit mix, floor area) lives in the application PDFs, read them with **Claude
   in-session, not Ollama** (see the pdf-parsing memory). Rules:
   - Fetch everything the HTML gives first; a document-bearing row is a PDF candidate when it is
     low-signal **or** missing a valuable field (parking / unit mix / floor area).
   - `select_pdf_sources` orders the documents best-first (e.g. Development Permit Board / Staff
     Report → architectural / parking drawings → civil → narrative reports). Read them **in order;
     if the first PDF has no usable info, move to the next**, and so on.
   - **Gather fields across EVERY PDF and page, not just the first usable one (general rule).** One
     document often holds only part of the picture - a site plan gives floor area but no height, the
     elevations give the building height and parking, and a separate "Project Stats / Site Stats /
     Development Data" summary sheet gives the civic address, FAR and zoning. Merge the fields from
     all of them onto the one row. A multi-sheet architectural set (cover, site-stats, site plan,
     elevations, sections) is one document with many pages - scan the whole set, not page 1.
   - **When the folder's own page and PDFs are thin, read its related ACTIVE applications (general
     rule).** A Prospero folder (a DP, DV, SA or RO) often carries little itself while its sibling
     rezoning / development-permit folder holds the full staff report and architectural set. If the
     folder's own documents do not yield the fields, fetch the **HTML and PDFs of each related
     ACTIVE application** (`select_pdf_sources` already appends them, rezoning first) and read those.
     Switch PDFs when one is thin: try the next document, then the next related folder. (e.g. Langley
     7687 200 St: the related DP101440 staff report states the project is "four 6-storey apartment
     buildings, 620 units"; 8045 198A St: the related RZ100594 report gives the two 4-storey office
     buildings and their floor areas.)
   - **Most of the real data sits in a summary table.** Look for a **Project Data / Project
     Statistics / Site Statistics / Unit Summary / Apartment Unit Summary / Zoning Bylaw Analysis**
     table, or the staff report's **proposal prose** ("The project is comprised of ...") - that is
     where units, unit mix, floor area / FSR, lot area, coverage, parking and height are stated
     together. Read the table, not just the drawing.
   - **A missing address is filled from the PDF (general rule).** When the HTML/list has **no civic
     address, or a placeholder** ("N/A", "-", a blank), take it from the PDF's **Project Summary /
     Site Stats / Development Data table**, then the **site plan**, then the elevations. (e.g. Langley
     DP101531: the Site Stats sheet states "8500 200th Street"; DP101451: the cover/A0.01 lists all
     seven civic addresses; RO100218: the drawings give "80 Avenue & 216 Street".) `apply_claude_pdf`
     treats a placeholder as missing (see `pdf_enrich._is_placeholder`) and fills `address` like any
     other field.
   - Apply the Claude-read fields with `pdf_enrich.apply_claude_pdf` (shared): deterministic HTML is
     never overwritten, each PDF field is stamped `pdf_claude`, and a genuine **HTML-vs-PDF
     disagreement is logged to `row['conflicts']`** (the Victoria conflict mechanism), not dropped.
   - A tabular project-data sheet needs **visual** reading: render the page with pymupdf
     (`get_pixmap(dpi=N).save(png)`) and read the PNG, rather than relying on fragmented text.
   - **Build the read worklist with `python tools/langley_pdf_queue.py` (Langley).** The deterministic
     parser only reads HTML, so it flags a thin folder `needs_pdf_extraction` but never opens a PDF or
     follows a related permit - that read is this manual Claude pass. The queue tool drives
     `select_pdf_sources` over every un-read flagged row (one representative per project), downloads and
     keyword-scans each candidate PDF (own documents, then ACTIVE related applications rezoning-first),
     renders the highest-signal project-data page to PNG, and writes `pdf_queue/langley/worklist.json`.
     It is the repeatable "traverse to the related docs and surface the data page" step - a row with
     zero own documents still reaches its rezoning/sibling-DP report (e.g. DP101299 -> DP101440's
     620-unit report). Flags: `--permit <ids>`, `--limit N`, `--all`, `--out DIR`, `--dpi N`. Read the
     rendered pages, then wire the fields into `tools/langley_enrich.py`.
   - Keep the per-project Claude-read fields in a `tools/<muni>_enrich.py` so the enrichment is
     auditable and reproducible (see `tools/westvan_enrich.py`, `tools/ubc_enrich.py`). Confirmed
     deterministic class mis-parses go in that file's `CLASS_OVERRIDE` (forced after the merge, since
     `apply_claude_pdf` never overwrites a deterministic class); keep it tiny and verified.
6. **De-duplicate by address (general rule - every municipality).** End `harvest()` by calling
   `bc_dev_permits.consolidate.consolidate_by_address(rows)`. One physical site often appears as
   several rows - an Official Plan Amendment, a Zoning By-law Amendment and a Subdivision filed
   together, or the same address published twice with different punctuation ("125 McDonald Blvd."
   vs "125 McDonald Blvd"). The rule groups rows by normalized civic address and folds:
   - **thin records** (no `units_total` / `number_of_stories` / `floor_area` program) into the
     strongest record at that address, and
   - **strong records that share a program signature** (the same project under several permit
     types - a Subdivision and a ZBA both stating 1205 units) into one.

   Folded records survive as the keeper's `related_permits` (id / type / status) with milestones
   merged. Strong records with *different* programs are left separate, so an address with genuinely
   distinct developments keeps them ("scores low → combine; scores high and distinct → separate").
7. **Upsert** into `dev_permit` keyed on `(municipality, permit_id)`; attach any
   documents to `dev_document`. Record `extraction_method`, `extraction_confidence`,
   and `source_url` on every row for provenance.
8. **QA the output.** Save to `data/processed/<municipality>.json`, then run `make qa`
   and open the viewer at http://localhost:8000/tools/qa/ (or use its "Load JSON" button).
   Review the `needs_pdf_extraction`, `needs_review`, and low-confidence rows, and confirm
   each row's `raw_text` actually matches the parsed fields.

## Field extraction contract

The extractor (whether run over page prose or PDF text) must return JSON matching the
schema in `references/extraction_prompt.md`. Rules:

- Any field not stated in the text → `null` (never guessed).
- `unit_mix` is an object of type→count (e.g. `{"1-bed": 19, "2-bed": 12}`); set
  `units_total` to the stated total.
- `occupancy_types` is an **array** - a mixed-use building has several
  (e.g. `["commercial", "residential"]`). See schema notes on why this is a child
  table, not sparse columns.
- Overall `confidence < 0.6` → set `needs_review = true`.
- **Read the whole context; don't let a scalar misrepresent a complex project (general rule).**
  A big project often states several figures, and the deterministic extractors in `features.py`
  read them in context:
  - **Storeys = the tallest element.** Towers over a lower podium ("four to six storey podiums ...
    towers from 30 to 40 storeys") → 40, not the podium's 6.
  - **Units = the stated grand total**, taken before a breakdown is itemized ("1,050 residential
    units, including 850 condo, 110 rental and 125 affordable") → 1,050, not a sub-figure.
  - **Parking keeps its full context.** `parking_notes` captures every parking/bicycle sentence
    (resident + visitor + commercial + long/short-term bike), since one integer can't hold the
    breakdown.
  - **Floor area by use** is kept as structured `occupancies` rows (residential / commercial /
    open space), not flattened to one number.
  - Always keep the source narrative in `raw_text`, so anything the scalars don't capture survives
    for re-extraction.

## Scope / hygiene

- Only public data. Rate-limit, cache by checksum, identify the bot, respect robots.
- Store raw source text (`raw_text`) so fields can be re-extracted later with a better
  model without re-crawling.

See `.claude/CLAUDE.md` for the project brief and rules, and `docs/COUNCIL.md` for the
per-municipality registry and exact selectors/endpoints.