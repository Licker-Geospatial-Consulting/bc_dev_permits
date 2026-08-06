# Development Permit Scraper — Project Instructions

Harvest development-permit and development-application data from BC municipal sources
into one normalized SQL table, covering **commercial, residential, mixed-use,
industrial, and institutional** projects with a single schema. Different cities publish
in very different ways (HTML pages, ArcGIS/VertiGIS webmaps, PDFs) — this project hides
that behind one shared data model and one extraction contract.

## How the pieces fit

```
docs/INSTRUCTIONS.md   ← you are here: the brief + the rules
docs/SKILL.md          ← the Agent Skill entry point (what Claude loads first)
docs/COUNCIL.md        ← registry: one row per municipality → which source_type + URLs
references/            ← loaded on demand:
   html_detail_pages.md    arcgis_webmaps.md    vertigis_webmaps.md    ollama_pdf_extraction.md
   schema.sql           ← the target table
   extraction_prompt.md ← the JSON contract
bc_dev_permits/        ← the package:
   features.py            ← deterministic prose → fields (regex/selectors, no LLM)
   modeling/predict.py    ← local Ollama PDF/prose → JSON fields
   harvesters/            ← per-municipality scrapers
```

This is a real Agent Skill: `SKILL.md`'s frontmatter is what makes Claude reach for it,
and it uses **progressive disclosure** — the reference for a given city loads only when
that city is scraped, so context stays small.

## The core rule

**Parse the fields from the page; only fall back to PDFs when the page has no text relevant to the fields required.**

- Content is in the HTML page or map feature (prose, `<div>`s, tables, attributes) → parse it 
  deterministically with `bc_dev_permits/features.py` (regex/selectors, no LLM) into `dev_permit`.
- A field isn't stated → leave it `NULL` (never fabricate).
- The page has no usable text at all → store the document links in `dev_document`, set
  `needs_pdf_extraction = true`, and let the local Ollama pass fill the fields from the
  PDFs later.

This keeps the cheap, deterministic path common and the expensive local-LLM path rare.

## What we capture (per `references/schema.sql`)

**Core:** address, latitude/longitude ("lot lon"), development name, permit type,
development_class (commercial vs residential vs mixed/industrial/institutional), status,
floor area, footprint area, number of storeys, units_total + unit_mix ("count of unit
types"), occupancy/building types (a **child table** because a mixed-use building has
several — see the note in schema.sql), dates (child table of milestones), url, permit_id,
rental-or-strata (+ subtype), parking (vehicle stalls, bike stalls, notes).

**Optional:** building materials, retrofit info, zoning density (FSR/FAR), energy info
(Step Code / LEED), mechanical-system info.

Every row also carries provenance: `source_url`, `raw_text`, `extraction_method`,
`extraction_confidence`, `checksum`, `needs_review`.

## Pipeline

1. **Registry** — read the municipality's row in `COUNCIL.md` → `source_type`.
2. **Harvest** the application list (HTML index, or an ArcGIS/VertiGIS layer query).
3. **Enrich** each application:
   - parse prose/attributes → fields, and/or
   - store PDF links and flag `needs_pdf_extraction` (fallback).
4. **Classify** `development_class` for every row.
5. **PDF pass (Ollama, fallback only)** — for rows flagged `needs_pdf_extraction`, 
  read the fields out of the PDFs with `bc_dev_permits/modeling/predict.py` (local, private).
6. **Upsert** into `dev_permit` on `(municipality, permit_id)`; attach `dev_document`,
   `dev_permit_milestone`, `dev_permit_occupancy`.
7. **Change detection** — skip unchanged sources by comparing `checksum`.

## Source-type cheatsheet

- **html** (North Van, West Van, UBC, New West project pages): index → detail pages →
  prose + milestones/PDF table. Plain HTTP + HTML parser; Playwright only if JS-rendered.
  → `references/html_detail_pages.md`.
- **arcgis** (Coquitlam, Port Moody, New West list): the map is a UI over an ArcGIS
  FeatureServer. Query the REST `/query` endpoint for all features + geometry; parse the
  `description`/`purpose` attribute. Handle multiple features / related records per
  polygon. → `references/arcgis_webmaps.md`.
- **vertigis** (Maple Ridge): VertiGIS Studio over ArcGIS services; a polygon can hold
  multiple application IDs via related records (`queryRelatedRecords`).
  → `references/vertigis_webmaps.md`.

## Local Ollama extraction (private, no data leaves the machine)

`bc_dev_permits/modeling/predict.py` turns prose or PDF text into schema JSON using a local
model with JSON-schema-constrained output. Scanned PDFs fall back to a local vision model.
Setup and wiring: `references/ollama_pdf_extraction.md`. Contract and worked example:
`references/extraction_prompt.md`.

## Adding a new municipality

1. Add a row to `COUNCIL.md` with its `source_type` and URLs.
2. If it's a new civic platform, add a `references/<platform>.md`; otherwise reuse one.
3. Confirm selectors / the FeatureServer `/query` URL in the browser and fill the TODOs.
4. Run harvest → enrich → upsert. No schema changes needed — every city maps to
   `dev_permit`.

## Hygiene

Public data only. Rate-limit per host, cache by checksum, send a descriptive
User-Agent, respect robots.txt. Development-application text can include personal names
(applicants/owners) — decide what you store and surface before republishing.
