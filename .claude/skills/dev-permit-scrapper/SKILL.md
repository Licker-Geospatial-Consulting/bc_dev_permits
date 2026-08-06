---
name: dev-permit-scrapper
description: Scrape development permit and development application data from BC municipality websites into a normalized SQL table, covering commercial, residential, mixed-use, industrial, and institutional projects. Use this whenever the user wants to extract, harvest, or parse development permits, development applications, rezonings, or DVPs from a city/municipal/council website or webmap — including HTML application pages, ArcGIS or VertiGIS webmaps, and PDF documents. Also use when the user mentions a specific municipality by name (North Vancouver/CNV, Coquitlam, Maple Ridge, Port Moody, West Vancouver, New Westminster, UBC) in the context of building or development data, or asks to set up local PDF field extraction with Ollama. Prefer parsing fields directly from the page; only fall back to saving PDF links when the page has no relevant text with respect to fields that needs to be fetched.
---

# Development Permit Scraper

Harvest development permit / development application data from BC municipal
sources into one normalized SQL table (`dev_permit`), regardless of the site's
architecture. Cover **commercial vs residential** (plus mixed-use, industrial,
institutional) with the same schema. The code should be ruff formatted.

## The one rule that shapes everything

**Parse first, PDF-fallback second.** For every application:

1. Try to extract the schema fields deterministically from the page's own text (prose paragraphs,
   `<div>`s, tables, or a webmap feature's attributes).
2. If the page has **no relevant text with respect to fields that needs to be fetched**, do not fabricate it — leave it
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
3. **Harvest** the list of applications, then **enrich** each one (parse fields or
   store PDF links per the rule above).
4. **Classify** `development_class` (commercial / residential / mixed / industrial /
   institutional) — this is a required output for every row.
5. **Extract prose → fields.** Parse fields that are already in the HTML/map content deterministically 
   (`bc_dev_permits/features.py` — regex/selectors, no model call). Reserve Ollama (`bc_dev_permits/modeling/predict.py`, 
   contract in `references/extraction_prompt.md`) for the PDF-fallback path only — reading fields out of 
   PDF documents when a page had no usable text. See `references/ollama_pdf_extraction.md`.
6. **Upsert** into `dev_permit` keyed on `(municipality, permit_id)`; attach any
   documents to `dev_document`. Record `extraction_method`, `extraction_confidence`,
   and `source_url` on every row for provenance.

## Field extraction contract

The extractor (whether run over page prose or PDF text) must return JSON matching the
schema in `references/extraction_prompt.md`. Rules:
- Any field not stated in the text → `null` (never guessed).
- `unit_mix` is an object of type→count (e.g. `{"1-bed": 19, "2-bed": 12}`); set
  `units_total` to the stated total.
- `occupancy_types` is an **array** — a mixed-use building has several
  (e.g. `["commercial", "residential"]`). See schema notes on why this is a child
  table, not sparse columns.
- Overall `confidence < 0.6` → set `needs_review = true`.

## Scope / hygiene

- Only public data. Rate-limit, cache by checksum, identify the bot, respect robots.
- Store raw source text (`raw_text`) so fields can be re-extracted later with a better
  model without re-crawling.

See `docs/INSTRUCTIONS.md` for the full project brief and `docs/COUNCIL.md` for the
per-municipality registry and exact selectors/endpoints.
