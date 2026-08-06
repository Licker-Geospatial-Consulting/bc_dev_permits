# Reference: HTML application detail pages

Covers municipalities whose applications are individual web pages: **North Vancouver
(CNV), West Vancouver, UBC, and the New Westminster project pages**. All follow the
same shape: a list/index page → per-application detail pages → a prose block + a
document/milestone table.

## Pattern

```
list page ──> [application links] ──> detail page
                                        ├── prose paragraph(s)                         → deterministic parse (features.py)
                                        ├── milestones table                           → dates + PDF links
                                        └── (no prose or no field to extract?)         → store PDF links only
```

## Steps

1. **Get the application links** from the list page. Grab anchors inside the
   content region. 
   - For North Vancouver - look under "Active Applications" title
   - For West Vancouver - look under "Active Development Applications" title
   - For New Westminster - look under "What's being proposed?" section
2. **For each detail page**, isolate the main content container (the article/main
   region, not header/nav/footer). Then:
   - **Prose** → concatenate the descriptive `<p>`/`<div>` text into `raw_text` and parse the fields 
      deterministically with `bc_dev_permits/features.py` (regex — no LLM).
   - **Address** → usually the page `<h1>`/title and breadcrumb (e.g. "115 East 18th
     Street"). Take it deterministically; don't rely on the LLM for it.
   - **Milestones table** → each row is (Milestone, Date, Documents, How to
     Participate). Write one `dev_permit_milestone` row per milestone; write each
     Documents anchor to `dev_document` (title = link text, url = href).
   - **permit_id** → often only present inside the PDF filenames (e.g.
     `PLN2025-00010-115-E-18th-St-...`). Regex it from document URLs when absent from
     the prose: `PLN\d{4}-\d{5}`, `DP\d+`, `REZ\d+`, etc. (varies by city — see COUNCIL.md).
3. **Fallback rule** — if the main content has no descriptive prose or if the fields to 
   be fetched are absent (the user's 651 East 1st example), do not force extraction: 
   set `is_parsed=false`, `needs_pdf_extraction=true`, store whatever links exist in 
   `dev_document`. The Ollama PDF pass fills fields later.
4. **development_class** — infer from prose + which list section the item came from.
   CNV explicitly splits "Major (6+ residential units / non-residential incl.
   Industrial, Commercial & Institutional)" vs "Minor (1-5 residential units)"; carry
   that hint into classification.

## Fetching JS-light vs JS-heavy

CNV/West Van/UBC detail pages are server-rendered — plain HTTP + an HTML parser
(selectolax/BeautifulSoup) is enough. Use Playwright only if a page renders its body
via JavaScript. Keep a per-domain rate limit and a descriptive User-Agent.

## Worked reference (CNV 115 East 18th Street)

- **Address** from `<h1>`: "115 East 18th Street".
- **Prose** (one paragraph) parsed deterministically by `bc_dev_permits/features.py`, yields: 
  permit_type=Rezoning, 6 storeys, 40 units,
  unit_mix {1-bed 19, 2-bed 12, 3-bed 3, suite 6}, rental (4 mid-market + 36 market),
  21 vehicle + 56 bike stalls. This exact case is the fixture in `tests/test_north_van.py`
- **Milestones table** yields dates ("Application Accepted" 2025-10-23, "Virtual
  Developer Information Session" 2026-02-05, ...) and PDF links (Architectural Drawings,
  Landscape Drawings) → `dev_document`.
- **permit_id** `PLN2025-00010` recovered from the drawing PDF filename.

## Per-city selector notes

Fill these in `COUNCIL.md` as you confirm them in the browser. Start points:
- **North Van**: list at `/Business-Development/Building/Land-Use-Approvals/Active-Applications`;
  scrape both the "Major Applications" and "Minor Applications" groupings.
- **West Van**: list at `/business-development/development-applications/active-development-applications`;
  detail pages under `/business-development/development-applications/<slug>`; same
  prose-or-PDF logic.
- **UBC**: list at `planning.ubc.ca/planning-development/development-projects`; iterate
  ALL pages (paginated) and take every development-permit entry; parse prose `<div>`s.
- **New West**: the project *pages* are HTML (parse prose/divs); the project *list*
  comes from the ArcGIS Experience "Projects List" — see `arcgis_webmaps.md` for how to
  pull that list, then treat each linked page with this reference.
