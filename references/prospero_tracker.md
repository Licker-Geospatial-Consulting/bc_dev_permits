# Reference: Prospero / "OurCity" development trackers (ASP.NET)

Covers municipalities that publish applications through a **Prospero** permit-tracker
portal (URL shape `…/webapps/ourcity/prospero/…`). **Victoria** is the first; the
platform is a vendor product, so expect other BC municipalities to reuse this flow.

The *detail* pages are ordinary server-rendered HTML and parse exactly like
`html_detail_pages.md` (prose → `bc_dev_permits/features.py`, a milestones table). What
is platform-specific — and lives here — is the **list retrieval** and the **record
model** (folder-numbered detail URLs, one application spanning many addresses,
concurrent-application cross-references).

## Pattern

```
search.aspx (ASP.NET grid) ──filter ACTIVE, page through──> [folderNumber, address, type, status]
        │
        └─> Details.aspx?folderNumber=<FOLDER> ─┬── Project Type      → permit_type
                                                ├── Status            → status
                                                ├── address(es)       → primary + rest into raw_text
                                                ├── Purpose / description prose → features.py
                                                ├── Task Progress rows          → dev_permit_milestone
                                                └── Related Applications (REZ…)  → cross-ref / fallback
```

## 1. List retrieval (`search.aspx`)

- **Default view** shows ~20 rows (active **and** archived). Each row: address, folder
  number, application type, application date, status, a short description, and a
  **Details** button linking to `Details.aspx?folderNumber=<FOLDER>`.
- **Filter to ACTIVE** via the "Application Status" sidebar. Victoria defines an active
  application as one that **has not yet received a decision by Council** — so the active
  set never carries an "approved"/"refused" status; those move to archived.
- **Pagination is a stateful ASP.NET AJAX UpdatePanel**, not URL-addressable pages
  (`« 1 2 3 4 5 »`). The numbered links run `pagination(N)`, which sets
  `ctl00$FeaturedContent$PageNumberHidden = N` and posts the
  `ctl00$FeaturedContent$PageNumber` control through the `updpnl_searchPage` panel
  (`__ASYNCPOST=true`, header `X-MicrosoftAjax: Delta=true`).
  - **Replaying the postback over plain HTTP does NOT work:** the server rejects the
    scripted postback and returns a `pageRedirect` to its ErrorPage (confirmed with a
    valid, freshly fetched `__VIEWSTATE`/`__EVENTVALIDATION` and both minimal and full
    payloads). A `requests` session can only ever read the first page.
  - **So the harvester pages with Playwright** (the `[browser]` extra;
    `bc_dev_permits.harvesters.victoria._browser_list_pages`). One catch: the page ships a
    pager that calls `__doPostBack` but never emits it (nor the
    `__EVENTTARGET`/`__EVENTARGUMENT` hidden inputs), so pagination is dead even in a real
    tab until you inject the stock `__doPostBack`. The harvester injects it, calls the
    site's own `pagination(N)`, and re-injects after each postback (the postback does a
    full-page reload). If Playwright is not installed it degrades to the first page and
    warns. Requires `pip install -e ".[browser]"` then `playwright install chromium`.
- Collect the **folder numbers** for every ACTIVE row on each page reached; the folder
  number is the stable id and the only thing the detail step needs.

## 2. Detail page (`Details.aspx?folderNumber=<FOLDER>`)

Plain server-rendered GET — a normal HTTP fetch + HTML parser is enough. Map fields:

| schema field        | source on the page                                                        |
|---------------------|---------------------------------------------------------------------------|
| `permit_id`         | the **folder number** (e.g. `DPV00297`) — from the query string, no PDF-filename recovery needed |
| `permit_type`       | the **"Project Type"** field verbatim (e.g. "Development Permit with Variance") |
| `status`            | the **"Status"** field (`ACTIVE` for the active set)                      |
| `address`           | the **primary address**; see multi-address note below                     |
| `raw_text`          | the Application Date + Status lines + the **Purpose/description** prose    |
| `number_of_stories`, `units_total`, `unit_mix`, `rental_or_strata`, `development_class` | deterministic parse of `raw_text` with `bc_dev_permits/features.py` |
| milestones          | the **"Task Progress"** rows (e.g. "Application Received", start/completed dates) → one `dev_permit_milestone` each |

- **Multiple addresses per application.** One folder can list many addresses (e.g.
  `DPV00297` spans 9 Broughton/Fort St lots). The schema has a single `address` column,
  so store the **primary** address there and keep the **full list inside `raw_text`** so
  it is not lost. Emit exactly **one `dev_permit`** per folder — do not fan out per
  address.
- **development_class** from the description prose: "…with retail at ground level" +
  dwellings → `mixed`; "N purpose-built rental units" / "N unit multi-family" →
  `residential`; classify from the wording, not the folder prefix.

## 3. Concurrent-application fallback

Development-permit descriptions frequently read
`"… CONCURRENT WITH REZ#<n>. REFER TO REZONING FOR ALL APPLICATION MATERIALS."`, and the
detail page lists **Related Applications** (e.g. `REZ00901`). In that case the DP folder
itself carries no drawings/reports:

- Record the related `REZ` id (in `raw_text`; optionally follow its
  `Details.aspx?folderNumber=REZ…` page for documents).
- Set `needs_pdf_extraction = true` when no usable materials are attached to the DP
  folder — the supporting documents live under the rezoning folder.

### Which document to extract (low-signal rows)

When a row scores low, `bc_dev_permits.harvesters.victoria.select_pdf_source` picks one
PDF to hand to the Ollama extractor, deterministically:

1. **ACTIVE related application with a Details link?** → use THAT application's documents
   (the live application carries the real materials; e.g. `DPV00294` defers to its ACTIVE
   `REZ00896`).
2. **Otherwise** → use this page's own **Documents** section. (An ARCHIVED related app is
   ignored — its documents are stale; fall back to the page's own set.)
3. Within the chosen set, prefer the **most recent "letter to mayor/council"**; if there
   is none, take the **most recent document** of any kind. "Most recent" is by the date
   parsed from the document title (`YYYY-MM-DD …`, `Sept 18, 2020 …`), else the last
   listed (the tracker appends newer documents last).

Documents are `FileDownload.aspx?fileId=…&folderId=…` links whose visible text is the
title. `extract_via_pdf` then downloads the chosen file and calls
`bc_dev_permits.modeling.predict.extract_from_pdf` (needs the `[pdf]` extra + a running
Ollama server). Selection is unit-tested in `tests/test_victoria_pdf.py`; the LLM call is
not (it needs a local model).

## 4. permit_id / folder prefixes

Folder prefixes encode the application type: `DPV` (Development Permit with Variance),
`REZ` (Rezoning) are confirmed; `DP`, `DVP`, `HAP` (Heritage Alteration) and others are
likely — confirm the full set in the browser and record it in `COUNCIL.md`. Suggested
regex once confirmed: `(?:DPV|DP|DVP|REZ|HAP)\d{4,5}`.

## 5. Parser notes (from Victoria examples)

The description prose drives the numeric fields. A few phrasings to make sure
`features.py` covers (add fixtures for each):

- "six storey residential building with retail at ground level" → 6 storeys, `mixed`.
- "129 new purpose built rental units" → `units_total` 129, rental (purpose-built).
- "6 story, 51 unit multi-family development" → 6 storeys, `units_total` 51.
- "3 storey, 3 unit strata houseplex" → 3 storeys, `units_total` 3, strata.
- "2 triplex buildings" → 6 units. **Note:** the current `_units_plex` maps a single
  "triplex" → 3 but does not multiply by a leading count; a "N <plex> buildings" phrasing
  needs a small parser extension before this yields 6 rather than 3.

## Fetching

Detail pages: plain HTTP + HTML parser. The list: prefer replaying the ASP.NET postback;
fall back to Playwright only if needed. Keep a per-host rate limit and a descriptive
User-Agent, and cache raw HTML by checksum like the other sources.
