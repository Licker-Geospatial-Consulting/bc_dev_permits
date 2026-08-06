# Council Registry

One entry per municipality. `source_type` selects the reference file the skill loads.
Fill the `TODO` blanks (exact selectors, service URLs, field mappings, id regexes) as
you confirm them in the browser — the entries below capture everything known so far and
the exact instructions you gave per site.

| slug        | Municipality              | source_type | reference file            |
|-------------|---------------------------|-------------|---------------------------|
| north_van   | City of North Vancouver   | html        | html_detail_pages.md      |
| coquitlam   | City of Coquitlam         | arcgis      | arcgis_webmaps.md         |
| maple_ridge | City of Maple Ridge       | vertigis    | vertigis_webmaps.md       |
| port_moody  | City of Port Moody        | arcgis      | arcgis_webmaps.md         |
| west_van    | District of West Vancouver| html        | html_detail_pages.md      |
| new_west    | City of New Westminster   | arcgis+html | arcgis_webmaps.md (+html) |
| ubc         | UBC (Campus + Community Pl)| html        | html_detail_pages.md      |

---

## north_van — City of North Vancouver  (html)

- **List:** https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Active-Applications
- **Scrape both groupings:** "Major Applications (6+ residential units / non-residential)"
  — which includes **Industrial, Commercial & Institutional** — AND "Minor Applications
  (1-5 residential units)".
- **Detail page** (example: `.../Active-Applications/115-East-18th-Street`):
  - Address ← page `<h1>` / breadcrumb.
  - Prose paragraph(s) in the main content region → parse deterministically (`harvesters/fields.py`).
  - "Application Process & Information" table → milestones (dates) + PDF links (Documents column).
  - **Fallback:** pages with no relevant information for the field required (example: `.../651-East-1st-Street`, The Trails
    Future Phases) → store PDF links in `dev_document`, set `needs_pdf_extraction=true`.
- **development_class hint:** which grouping the item came from (Major non-residential →
  commercial/industrial/institutional; Minor → residential).
- **permit_id regex:** `PLN\d{4}-\d{5}` (recover from PDF filenames when absent from prose).


## coquitlam — City of Coquitlam  (arcgis)

- **App:** https://experience.arcgis.com/experience/346bbb6ec0024422abcf27b0e338dbad/page/Page?org=Coquitlam
- ArcGIS Experience Builder over a FeatureServer. Click a polygon → popup = feature
  attributes.
- **Multiple features per polygon:** a single polygon can return more than one feature
  — parse **all** descriptions/features, one `dev_permit` each.
- Parse the popup `description` attribute deterministically with `harvesters/fields.py`
- capture FeatureServer `/query` URL from Network tab; record layerId + field names
  (project number field — e.g. the "22-039" id — description, status, dates).

## maple_ridge — City of Maple Ridge  (vertigis)

- **App:** https://apps.vertigisstudio.com/web/?app=8b409970fec048b0940b60fe1e225e39
- VertiGIS Studio Web over ArcGIS services (see vertigis_webmaps.md).
- **Multiple application IDs per polygon:** use `queryRelatedRecords` — emit one
  `dev_permit` per application in the polygon's "Application details" section.
- capture layer URL + relationshipId + the "Application details" field names.

## port_moody — City of Port Moody  (arcgis)

- **App:** https://portmoody.maps.arcgis.com/apps/webappviewer/index.html?id=d42a4cd7ece44d2d8dbf759cdcdac203
- ArcGIS Web AppViewer over a FeatureServer.
- Parse the `purpose` attribute for the fields with `harvesters/fields.py` (deterministic parse).
- capture FeatureServer `/query` URL + confirm the `purpose` field name and id/date fields.

## west_van — District of West Vancouver  (html)

- **List:** https://westvancouver.ca/business-development/development-applications/active-development-applications
- **Detail pages** (examples: `.../11-3085-deer-ridge-close`, `.../1337-ottawa-avenue-rezoning`):
  : parse prose paragraphs/`<div>`s → parse deterministically (`harvesters/fields.py`).
- **Fallback:** pages with no relevant information for the field required → store PDF links, `needs_pdf_extraction=true`.
- the main-content selector is div with class id `view-content`.

## new_west — City of New Westminster  (arcgis + html)

- **App (Projects List):** https://experience.arcgis.com/experience/c30fde314b964efaa1623e099be8da40
- The **"Projects List"** widget is backed by a layer/table → query it to get project
  rows and their **detail-page URLs** from Project Address hyperlink.
- Then treat each linked project page with **html_detail_pages.md**: parse prose
  paragraphs/`<div>` sections; PDF-fallback when empty.
- capture the Projects-List layer/table `/query` URL and the field holding the
  project page link.

## ubc — UBC  (html)

- **List:** https://planning.ubc.ca/planning-development/development-projects
- Iterate **all pages** (pagination) and take **every development-permit entry**
  (examples: "ANSO Renewal - DP24027 Amendment 2 - MAC Expansion", "Wesbrook Place South
  Lot 8 & 9 - DP26024").
- Parse prose paragraphs/`<div>` sections on each project page → parse deterministically (`harvesters/fields.py`).
- **permit_id regex:** `DP\d{5}` (+ amendment suffix).
- Note: UBC is federally/provincially planned (not a BC municipality proper) but fits the
  same html flow. development_class from prose (academic/institutional vs residential).
- pagination mechanism through card selector, can be fetched under class `view-content`
  and the next page is accessed by nav class `pager`
