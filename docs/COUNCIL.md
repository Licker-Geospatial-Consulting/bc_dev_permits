# Council Registry

One entry per municipality. `source_type` selects the reference file the skill loads.
Fill the `TODO` blanks (exact selectors, service URLs, field mappings, id regexes) as
you confirm them in the browser — the entries below capture everything known so far and
the exact instructions you gave per site.

| slug        | Municipality               | source_type | reference file            |
|-------------|----------------------------|-------------|---------------------------|
| north_van   | City of North Vancouver    | html        | html_detail_pages.md      |
| coquitlam   | City of Coquitlam          | arcgis      | arcgis_webmaps.md         |
| maple_ridge | City of Maple Ridge        | vertigis    | vertigis_webmaps.md       |
| port_moody  | City of Port Moody         | arcgis      | arcgis_webmaps.md         |
| west_van    | District of West Vancouver | html        | html_detail_pages.md      |
| halton_hills| Town of Halton Hills (ON)  | arcgis+html | arcgis_webmaps.md (+html) |
| new_west    | City of New Westminster    | arcgis      | arcgis_webmaps.md (+beheard) |
| ubc         | UBC (Campus + Community Pl)| html        | html_detail_pages.md (+claude-pdf) |
| surrey      | City of Surrey             | pdf         | (planning report PDFs, claude-read) |
| victoria    | City of Victoria           | prospero    | prospero_tracker.md       |

---

## north_van — City of North Vancouver  (html)

- **List:** https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Active-Applications
- **Scrape both groupings:** "Major Applications (6+ residential units / non-residential)"
  — which includes **Industrial, Commercial & Institutional** — AND "Minor Applications
  (1-5 residential units)".
- **Detail page** (example: `.../Active-Applications/115-East-18th-Street`):
  - Address ← page `<h1>` / breadcrumb.
  - Prose paragraph(s) in the main content region → parse deterministically (`bc_dev_permits/features.py`).
  - "Application Process & Information" table → milestones (dates) + PDF links (Documents column).
  - **Fallback:** pages with no relevant information for the field required (example: `.../651-East-1st-Street`, The Trails
    Future Phases) → store PDF links in `dev_document`, set `needs_pdf_extraction=true`.
- **development_class hint:** which grouping the item came from (Major non-residential →
  commercial/industrial/institutional; Minor → residential).
- **permit_id regex:** `PLN\d{4}-\d{5}` (recover from PDF filenames when absent from prose).


## coquitlam — City of Coquitlam  (arcgis)

- **App:** https://experience.arcgis.com/experience/346bbb6ec0024422abcf27b0e338dbad/page/Page?org=Coquitlam
- **FeatureServer layer (query this directly):**
  `https://services2.arcgis.com/Q6Lq3evZUGfPrN7o/arcgis/rest/services/Development_Information_Demo/FeatureServer/0`
  — layer 0 "Development_Information", polygon, ~484 features. Harvested with
  `?where=1=1&outFields=...&returnGeometry=true&outSR=4326&f=json`, paged by `resultOffset`.
- Discovered via the Experience item config:
  `https://www.arcgis.com/sharing/rest/content/items/<itemid>/data?f=json`.
- **Field mapping** (attribute → `dev_permit`), handled by `harvesters/coquitlam.py`:
  - `PROJECT_NUMBER` → `permit_id` (e.g. "23-078")
  - `ADDRESS` → `address`
  - `PROJECT_STATUS` → `status` (Active Application / Approved / Conditional Approval / Pre-Application / Declined)
  - `PROJECT_DESCRIPTION` → `raw_text`, parsed into fields by `bc_dev_permits/features.py`
  - `SUBMISSION_DATE` (epoch ms) → "Application Received" milestone
  - polygon geometry → `latitude`/`longitude` (ring centroid)
  - `APPLICANT` → **excluded** (personal name; no schema column, PII)
- `extraction_method = "arcgis"`. Bare lot subdivisions / lot-line adjustments / zoning-only
  rezonings state no building use and correctly land as `development_class = "unknown"`.

## maple_ridge — City of Maple Ridge  (vertigis)

- **App:** https://apps.vertigisstudio.com/web/?app=8b409970fec048b0940b60fe1e225e39
- VertiGIS Studio Web over an ArcGIS MapServer (see vertigis_webmaps.md). The service was
  found via the page's resource list (performance entries), not the app config.
- **MapServer (query this directly):**
  `https://geoservices.mapleridge.ca/server/rest/services/LandDevelopmentViewer/LandDevelopmentData/MapServer`
  - layer **0 DevelopmentApplications** (polygons) — one application each, ~6757 total (~823 `StatusType='Active'`).
  - table **2 ApplicationDetails** — key/value rows (`InfoDescription`/`InfoValue`), the
    "Application details" section.
  - table **1 Applicants** — personal names: **not fetched** (PII, no schema column).
- **No declared relationship:** layer 0 and table 2 join by the shared **`FolderRSN`** key.
  The harvester pages the applications, then bulk-fetches details for the harvested FolderRSNs
  (`FolderRSN IN (...)`, chunked) and groups them — no `queryRelatedRecords` needed.
- **Field mapping** (layer 0), handled by `harvesters/maple_ridge.py`:
  - `ReferenceFile` → `permit_id` (e.g. "2019-255-RZ"); `FolderName` = "<id> - <address>" → `address`
  - `FolderDesc` → `permit_type` (Rezoning / Development Permit / Subdivision / Variance Permit /
    Soil Deposit Permit / Agricultural Land Reserve / Tree Cutting Permit)
  - `Status` → `status`; `InDate` (epoch ms) → "Application Received"; First/Second/Third
    `ReadingDate` + `PublicHearingDate` ("YYYYMMDDHHMMSS") and `IssueDate` → milestones
  - geometry polygon → `latitude`/`longitude` (ring centroid)
- **Details → fields** (deterministic key/value mapping): `CD Residential Unit` or (for multi-unit
  RZ/DP) `Approx. Number of Proposed Lots/Units` → `units_total`; a Subdivision's "Lots/Units" is
  **lots, not units**, so it is left null. `Current Zoning`→`Proposed Zoning` → `zoning_density`;
  `Number of Proposed Rental Units` → rental; class derived from the type / zoning / OCP text.
- `extraction_method = "arcgis"`. Shared ArcGIS transport lives in `harvesters/arcgis_common.py`.

## port_moody — City of Port Moody  (arcgis + engage cross-reference)

- **App:** https://portmoody.maps.arcgis.com/apps/webappviewer/index.html?id=d42a4cd7ece44d2d8dbf759cdcdac203
- ArcGIS Web AppViewer over a hosted FeatureServer (found via the webappviewer item config →
  webmap `/data?f=json` → operationalLayers), handled by `harvesters/port_moody.py`.
- **FeatureServer (query this directly):**
  `https://services9.arcgis.com/XMHGoEc3tdJ8TphG/arcgis/rest/services/Development_Applications_and_Milestones_3_WFL1/FeatureServer`
  - layer **0 DevelopmentApplicationsView** (polygons, ~170 features → ~126 unique folders).
    Multiple polygons can share a `Folder_Number` (a master folder spans parcels), so rows are
    **deduped by `Folder_Number`** (keep the richest: has geometry, longest Purpose).
  - table **1 Milestones** keyed by `FOLDER_NUMBER` — process milestones; duplicated per parcel,
    so collapsed by (name, date) and ordered by date.
- **Field mapping** (layer 0):
  - `Folder_Number` → `permit_id` (e.g. "ORD00008"); `Address` (first line; "PORT MOODY BC
    <postal>" tail dropped) → `address`; `Type` → `permit_type`; `Folder_Status` → `status`
  - `Application_Date`/`Folder_Completion_Date` (epoch ms) → milestones; geometry → centroid lat/lon
  - `Subject`/`Purpose` → `raw_text`, parsed by `features.py`; `Applicant`/`File_Manager` →
    **excluded** (personal / staff names, PII, no schema column)
- **Engage cross-reference:** the map's `Purpose` is usually thin or empty for the large
  rezoning/OCP files — their building programs live on **Engage Port Moody**
  (https://engage.portmoody.ca/projects), one page per site. The harvester scrapes that listing
  into an address-keyed index, matches each application by **civic number + distinctive street
  token** (e.g. "80 ELECTRONIC AVE" → `.../80-85-electronic-avenue-...`), and parses the matched
  project's description prose with the same `features.py` (e.g. ORD00008: 983 units, 38 storeys,
  868 parking, mixed, strata; ORD00006: 48 rental apartments, 6 storeys, 885 m2 commercial, FSR 2.63).
  - The description region (`#project_description_text`) renders the body **twice** (a
    `.truncated-description` preview + a hidden `.full-description` copy) plus the title/share
    links; `engage_description` takes one full copy only, or counts double (48 becomes 96).
  - Trusted Engage overlays: `units_total`, `number_of_stories`, parking, `floor_area`,
    `rental_or_strata`, `zoning_density` (FSR/FAR), a concrete `development_class`, and per-use
    floor areas (e.g. 885 m2 commercial) as structured `occupancies`. `unit_mix`/`occupancy_types`
    are **not** overlaid (Engage parking prose like "181 commercial … spaces" misparses).
  - The Engage page URL each enriched row's rich fields came from is stored as `enrichment_url`.
  - Engage prose quirks that `features.py` handles deterministically (added for this source, shared
    by all): m2/ft2 render as superscripts that flatten to "m 2"; the gross floor area must beat
    indoor/outdoor amenity figures (largest non-amenity wins); FAR is phrased "floor area ratio"
    and "FAR of up to 4" (allowed max) must yield to the proposed "FAR of 2.79", never a storey
    count; units appear as "48 market rental apartments", "207 rental homes", "40 housing units";
    unit mix as "Four 1-bedroom units"; parking as "120 total vehicle" / "208 vehicular" spaces.
- **Address parsing:** the civic number is the number after any unit prefix - "24-2914 St Johns"
  is unit 24, civic **2914** (so it matches the "2914-2934 St Johns" project), and "A205B-205 Mount
  Royal" is civic 205. Matching is by civic + distinctive street token.
- **Consolidation (two passes):**
  1. *By Engage project* - several map folders (an OCP/rezoning master, its subdivisions, a TUP,
     etc.) can be the *same* project and match one Engage page. They are grouped by matched slug and
     collapsed into **one geocodable record**: the primary (the folder the Engage page names in
     "Application #...", else the most canonical permit type) carries the enriched program once.
  2. *By address* - thin administrative permits (subdivisions, minor DPs, variances) with no
     building program that share one civic address are absorbed into the strongest record there, so
     an address has one record. A record that carries a real program (units/storeys/floor area) or
     Engage enrichment ("score more") is kept separate, so an address with several distinct strong
     applications keeps them. Addresses without a civic number are never merged (too ambiguous).
  In both passes the folded siblings become `related_permits` (id/type/address/status/purpose) with
  milestones merged, and a primary with no polygon inherits a sibling's centroid.
  (170 features to ~126 folders to ~78 records.)
- `extraction_method = "arcgis"`, or `"arcgis+engage"` when enriched (always `needs_review`,
  with the Engage URL + description kept in `raw_text` for re-extraction).
- Shared ArcGIS transport lives in `harvesters/arcgis_common.py`.

## west_van — District of West Vancouver  (html + Claude-read PDF)

- **List:** https://westvancouver.ca/business-development/development-applications/active-development-applications
  (~21 active applications). Detail pages under `.../development-applications/<slug>`.
- **Cloudflare.** The whole site is behind Cloudflare bot protection, so a plain HTTP GET is
  refused ("Just a moment..."). `harvesters/west_van.py` fetches through headless Chromium
  (Playwright, the `[browser]` extra) with a **fresh context per page and a cool-down between
  pages** (Cloudflare rate-limits bursts), and caches each rendered page to disk. PDFs, by
  contrast, are plain static assets and download over normal HTTP.
- **Field mapping** (deterministic HTML, `parse_detail`): `<h1>` with the trailing descriptor
  stripped ("1337 Ottawa Avenue (Rezoning)" → `address` "1337 Ottawa Avenue"); prose paragraphs →
  `features.extract_all` (height now parses too: "from 18.2 metres to 19.6 metres" → `height_m`
  19.6); any process table → milestones; all `.pdf` anchors → `documents`; slug → `permit_id`
  fallback. A low-signal row is flagged `needs_pdf_extraction`.
- **PDF enrichment is Claude-read, not Ollama** (see the pdf-parsing memory). `select_pdf_sources`
  orders the documents **Staff Report → Architectural Drawings / Plans → Preliminary Drawings →
  other**; Claude reads them in priority order in-session (the first that yields project data
  wins) and the fields are applied with `apply_claude_pdf`, which reuses the shared
  `pdf_enrich` merge: deterministic HTML is never overwritten, a genuine HTML-vs-PDF disagreement
  is logged to `row['conflicts']`, each PDF field is stamped `pdf_claude`, and the row is
  `needs_review=True`. `extraction_method` is `"html"` or `"html+claude_read"`.
  - Worked examples: **11-3085 Deer Ridge Close** - HTML gives the height variance (19.6 m); the
    Plans PDF adds a 7-storey residential building (rooftop sauna amendment). **1337 Ottawa Avenue**
    - thin HTML; the Staff Report gives a 1,414.9 m2 RS3 site subdivided into two ~706 m2 lots, a
    single-family dwelling + secondary suite each (residential, 2). **1507 Bellevue Avenue** - HTML
    gives a 3,000 sq ft office addition; the Architectural Drawings add a 3-storey commercial
    building, 899.8 m2 footprint.
- Claude-read fields for a batch are applied with a small helper (`tools/westvan_enrich.py`); a
  full automated run is not wired into `dataset.py` ENRICHERS because the reads happen in-session.

## halton_hills — Town of Halton Hills, Ontario  (arcgis + html)

- **List (HTML):** https://www.haltonhills.ca/work/planning-development/active-development-applications
- **Map:** https://map.haltonhills.ca/HT5/Index.html?viewer=p.HT5 — a VertiGIS/Geocortex HTML5
  viewer over an ArcGIS MapServer. Service discovered via the Geocortex Essentials REST site
  config (`connectionString`), handled by `harvesters/halton_hills.py`.
- **MapServer (query this directly):**
  `https://map.haltonhills.ca/awse/rest/services/eP/MapServer`
  - layer **24 P_DevelopmentApplications** (polygons) — one application each, the comprehensive
    source (~118 features). Harvested with `?where=1=1&outFields=...&returnGeometry=true&outSR=4326&f=json`.
- **Field mapping** (layer 24), handled by `harvesters/halton_hills.py`:
  - `FILE_NO` → `permit_id` (e.g. "D12SUB12.001"; combined files keep the full string, e.g.
    "D09OPA24.001, D14ZBA24.008 & D12SUB24.001")
  - `LOCATION` → `address`; `FILE_TYP` → `permit_type` (Subdivision / OPA / ZBA / Site Plan / ...)
  - `LAND_USE` → `development_class` hint (Residential/Commercial/Industrial/Institutional/Mixed →
    class directly; "Other" falls back to whatever the prose classifies as, often `unknown`)
  - `APP_DESC` → `raw_text`, parsed into fields by `bc_dev_permits/features.py` (a subdivision's
    stated lot count becomes `units_total`; "108 townhouse unit" → `unit_mix`)
  - `CUR_STAT` → `status`; `REC_DATE` (epoch ms) → "Application Received" milestone
  - `TWN_AREA` (area of town) + `P_NX_STP` (next step) → appended to `raw_text`
  - polygon geometry → `latitude`/`longitude` (ring centroid)
  - `P_CONTAC` (planning contact name + phone) → **excluded** (staff name, PII, no schema column)
- **HTML cross-reference** (richer prose for the ~10 "major" applications): the list page links to
  per-application subpages whose Froala `.fr-view` prose is far richer than the map's terse
  `APP_DESC` (e.g. map: "apartment buildings with 6 residential towers"; subpage: "6 towers,
  17-22 storeys, 1,481 units, 1,481 parking"). `enrich_from_html` matches a subpage to a map row by
  **(civic number, distinctive street token)** first, then by a **shared file number** (some sites
  carry a legal-description address like "Part of Lot 21, Concession 9" that no civic match reaches,
  e.g. the Eden Oak subdivision). The richer prose wins per field; an empty field keeps the map's.
  `extraction_method` becomes `"arcgis+html"`, and `enrichment_url` records the subpage.
  - A subpage splits into Description / Process / Status / Submissions sections. The Submissions
    sections are **document lists** ("Contact X to request a copy of ...") and are dropped; the
    remaining prose is kept whole in `raw_text` so parking/amenity notes survive. The count fields
    **combine by MAX across sections, not sum** (each section restates the totals) — otherwise
    "82 total units" plus "81 townhouses + 1 detached" double-counts to 164. A stated grand total
    ("744 total dwelling units") and the "each containing N units" per-block breakdown are handled
    in `features.py` so a nested "<subtotal>, as follows: <sub-components>" list is not summed twice.
  - A major subpage that matches **no** map row (e.g. a just-filed application like 530 Guelph St)
    is added as its own `extraction_method = "html"` row (permit id from the file numbers, address
    from the title, null geometry, `needs_review`), so a new major application is never lost.
  - `_MAJOR_OVERRIDES` carries the few curated corrections prose cannot parse deterministically: a
    revision that *reduces* the program (16 & 18 Mill: two 4-storey buildings / 34 units, revised
    down from a 6-storey / 52-unit building) and a per-unit parking note (159 Confederation).
- **De-duplication** (the general rule, see below): `harvest()` ends by calling
  `consolidate.consolidate_by_address`, which collapses an OPA + ZBA + Subdivision filed for one
  site into a single record. Example: the Subdivision and Site Plan at "125 McDonald Blvd." / "125
  McDonald Blvd" become one record with the other kept in `related_permits`.
- `extraction_method = "arcgis"` (map only), `"arcgis+html"` (enriched) or `"html"` (standalone
  major). Shared ArcGIS transport lives in `harvesters/arcgis_common.py`. First Ontario source;
  still one normalized schema. Terse map-only Site Plans (telecom towers, quarry extensions,
  warehouses) legitimately carry no unit/floor program — the source publishes none.

## new_west — City of New Westminster  (arcgis + Be Heard engagement)

- **App ("Projects on the Go"):** https://experience.arcgis.com/experience/c30fde314b964efaa1623e099be8da40
- An ArcGIS Experience Builder app that embeds **Dashboards** over one hosted FeatureServer. Chain
  to find the service: experience item `/data` → embedded dashboard itemIds → each dashboard's
  Web Map (`893d4ba0…`, "POG by Type") → its single operational layer. Handled by
  `harvesters/new_west.py`.
- **FeatureServer (query this directly):**
  `https://services3.arcgis.com/A7O8YnTNtzRPIn7T/arcgis/rest/services/POG/FeatureServer/0`
  — layer 0 "POG", polygon, ~70 features. One row per project (its application files consolidated).
- **Field mapping** (attribute → `dev_permit`), handled by `harvesters/new_west.py`:
  - `Files` → `permit_id` (the application numbers, newline-separated, e.g. "PAR01366\nRZ000204\n
    DP000860" → "PAR01366, RZ000204, DP000860"); their prefixes → `permit_type`
    (RZ→Rezoning, DP/DPU/DPS/DPQ→Development Permit, DVP→Development Variance Permit, OCP→OCP
    Amendment, HER→Heritage, PAR→Pre-Application Review, TUP→Temporary Use Permit, SDP→Special
    Development Permit)
  - `Project_Address` → `address`; `Project_Type` → `development_class` (Residential → residential,
    "Mixed Use" → mixed, Commercial/Institutional/Industrial → their class)
  - `Project_Description` → `raw_text`, parsed into fields by `bc_dev_permits/features.py`
    ("55 unit infill townhouse development" → units; "29 storey ... 338 market rental units" →
    storeys + units + rental)
  - `Status_Edit` → `status` (the readable status, e.g. "E Building Permit Issued")
  - `Last_Updated` (epoch ms) → "Last Updated" milestone; polygon geometry → centroid lat/lon
  - `Project_Planner` → **excluded** (staff name, PII, no schema column)
- **Be Heard New West cross-reference** (richer prose, ~18/70 rows): the `Hyperlink` field holds a
  `beheardnewwest.ca` engagement link. That site is a JavaScript single-page app, so the page is
  rendered with headless Playwright (the `[browser]` extra) and cached. The engagement prose adds
  floor space ratio, parking and bicycle-stall counts the terse `Project_Description` omits
  (Cumberland St: "FSR of 1.0 ... 88 off-street parking ... 55 long-term bicycle ... four
  short-term"). A parsed value overlays the map row; an empty one keeps the map's.
  `extraction_method` becomes `"arcgis+beheard"` and `enrichment_url` records the page.
  - **The rule:** enrich only when a project **has a link** and that link resolves to a **real
    project page** (its title starts with "Project:"). A row with **no link**, or a link that
    resolves to a **"page not found"** (the generic platform title, e.g. the retired
    616-640-sixth-st page), is left as a plain `arcgis` row — no further parsing.
- De-duplication: `harvest()` ends with `consolidate.consolidate_by_address` (the general rule),
  though the FeatureServer already emits one row per project so duplicates are rare.

## ubc — UBC (Campus + Community Planning)  (html + Claude-read PDF)

- **List:** https://planning.ubc.ca/planning-development/development-projects — a Drupal view
  (`.view-project-view-page`) of project cards. Plain HTTP works (no Cloudflare). Handled by
  `harvesters/ubc.py`.
- **Project cards** are the links whose text carries a **"DP#####"** permit number (navigation
  links do not); the status badge ("Application" / "Permit Approved" / "Under Construction") is read
  from the same card text. `get_project_cards` returns `{url, permit_id, status}` per project.
- **Field mapping** (detail page), handled by `harvesters/ubc.py`:
  - `<h1>` "`<Name> - DP#####`" → `development_name` + `permit_id`; `permit_type` = "Development Permit"
  - the **Location** field → `address`; project prose → `bc_dev_permits/features.py`
    ("303 units", "14-storey market rental tower ... 6-storey", "total building area: 21,784.1 sq. m")
  - status from the list card; a milestone table (if present) → milestones
  - **Boilerplate is dropped:** the "the UBC Vancouver campus consists of 994 acres" about-UBC blurb
    (whose acreage would otherwise masquerade as a project area) and the staff contact block (a
    person's name + phone + email, which is PII) are filtered out of the prose and the Location.
- **Claude-read PDF enrichment** (not Ollama; see the pdf-parsing memory). A project with documents
  is a PDF-read candidate whenever it is low-signal **or** missing parking / unit mix / floor area -
  the richer fields the drawings carry. `select_pdf_sources` orders the PDFs **Development Permit
  Board report → architectural / parking drawings → civil / landscape → narrative reports**; Claude
  reads them in priority order in-session (the first that yields project data wins) and applies the
  fields with `apply_claude_pdf` (shared with West Van, in `pdf_enrich`): deterministic HTML is never
  overwritten, each PDF field is stamped `pdf_claude`, and a genuine HTML-vs-PDF disagreement is
  logged to `row['conflicts']` (the Victoria conflict mechanism). `extraction_method` becomes
  `"html+claude_read"`.
  - Worked examples (`tools/ubc_enrich.py`): **DP24018 Lot BCR 7** - the DP Board report adds 303
    vehicle + 745 bicycle stalls and the 189 tower / 114 faculty-staff unit split. **DP26024 Lots 8
    & 9** - the architectural project-data sheet adds 201 vehicle parking, 31,749 m2 floor area (by
    use), FSR 10.13. **DP25042 Kenny** - the architectural cover sheet shows a targeted seismic
    renovation: institutional, 0 new units, 0 new parking.
- De-duplication: `harvest()` ends with `consolidate.consolidate_by_address` (the general rule).
- Note: UBC is planned by UBC Campus + Community Planning (not a BC municipality proper) but fits
  the same HTML flow. `extraction_method` is `"html"` or `"html+claude_read"`.
  and the next page is accessed by nav class `pager`

## surrey — City of Surrey  (PDF-first, Claude-read)

- **List:** https://www.surrey.ca/city-government/council-meetings/planning-reports — a paginated
  Drupal view (`.views-row` inside `.view-search-planning-reports`, `?page=N`). Plain HTTP works.
  Handled by `harvesters/surrey.py`. **There are no HTML detail pages: each application is a council
  planning report PDF.** The list is a large archive (hundreds of reports), so harvest with a
  `--limit`; `--limit 0` fetches the whole archive (one PDF download per report, cached).
- **List row** → `{permit_id, address, status, meeting_date, report_date, pdf_url}`: the `<h4>` holds
  "Planning Report `<app no>`" (e.g. 7925-0315-00, also the PDF file name's stem), the address is the
  bare text node after it, the `<time>` is the council meeting date, and `<span class="tag">` the
  status (Active / Inactive). The PDF is at `/sites/default/files/planning-reports/PLR_<appno>.pdf`.
- **PDF page-1 PROPOSAL (deterministic, from the report's own text).** Every report opens with
  "Application No.", "PROPOSAL:" (a prose summary + bullet application types), "LOCATION:".
  `pdf_proposal` (pymupdf text) splits that into the description + the bullet permit types, and the
  description parses through `bc_dev_permits/features.py` ("a 24-storey residential building with 230
  rental units" → 24 storeys / 230 units / residential / rental; "a 3,922 square metre industrial
  building" → 3,922 m2 / industrial). A rezoning bullet's "from `<current>` to `<proposed>`" →
  `zoning_density`. `extraction_method = "pdf_text"`.
- **Claude-read data sheet** (not Ollama). The deeper pages (the "Development Considerations" parking
  table, unit mix) are read by Claude in-session - and rendered to an image when the text layer
  fragments the table - and applied with `apply_claude_pdf` (shared `pdf_enrich`): deterministic
  values are never overwritten, fields are stamped `pdf_claude`, and a disagreement is logged to
  `row['conflicts']` (the Victoria conflict mechanism). `extraction_method` becomes
  `"pdf_text+claude_read"`. Worked example in `tools/surrey_enrich.py`: **7925-0315-00** - the
  report's parking table adds 120 vehicle + 245 bicycle stalls to the 230-unit / 24-storey summary.
- De-duplication: `harvest()` ends with `consolidate.consolidate_by_address` (the general rule).

## victoria — City of Victoria  (prospero)

- **List (Development Tracker / Prospero):** https://tender.victoria.ca/webapps/ourcity/prospero/search.aspx
- **Scope:** ACTIVE applications only ("active" = not yet decided by Council), across all
  list pages. The list is a stateful ASP.NET AJAX grid whose pager rejects scripted
  postbacks, so it is walked with **Playwright** (the `[browser]` extra; needs
  `playwright install chromium`); detail pages are plain GETs. Without Playwright the
  harvester degrades to the first page and warns. See `prospero_tracker.md` for the
  `__doPostBack` injection quirk. A full harvest currently yields ~136 ACTIVE applications.
- **Detail page:** `https://tender.victoria.ca/webapps/ourcity/Prospero/Details.aspx?folderNumber=<FOLDER>`
  (server-rendered GET). Field mapping in `prospero_tracker.md`. In short:
  - **permit_id** ← the `folderNumber` (e.g. `DPV00297`).
  - **permit_type** ← the "Project Type" field (e.g. "Development Permit with Variance").
  - **address** ← primary; an application can list **many addresses** (one `dev_permit`,
    full list kept in `raw_text`).
  - **status** ← the "Status" field (`ACTIVE`).
  - **raw_text** ← Application Date + Status + description → parse with `features.py`.
  - **milestones** ← the "Task Progress" rows.
- **development_class** from the prose (retail at grade + dwellings → `mixed`; rental /
  multi-family → `residential`).
- **Concurrent rezonings:** descriptions like "CONCURRENT WITH REZ#xxxxx. REFER TO
  REZONING FOR ALL APPLICATION MATERIALS" mean the documents live under the linked `REZ`
  folder → record the id and set `needs_pdf_extraction=true`.
- **permit_id regex:** `(?:DPV|DP|DVP|REZ|HAP)\d{4,5}` — confirm the full prefix set in
  the browser.
- **Worked examples (description → expected fields), from the user:**
  - *"six storey residential building with retail at ground level"* → 6 storeys, `mixed`.
  - *932 Balmoral Rd* — "129 new purpose built rental units" → 129 units, rental.
  - *441 Government St* — "6 story, 51 unit multi-family development" → 6 storeys, 51 units.
  - *1171 & 1173 May St* — "2 triplex buildings" → 6 units (two addresses, one permit;
    needs the "N plex buildings" multiplier in `features.py`, see `prospero_tracker.md`).
  - *1320 Purcell Pl* — "3 storey, 3 unit strata houseplex" → 3 storeys, 3 units, strata.
- **Note:** the earlier "status = approved" example conflicts with the site — active
  applications show `Status: ACTIVE` (no Council decision yet), so status is stored as
  `ACTIVE`, not "approved".
