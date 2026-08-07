# Development Permit Scraper

Harvest public development permit application data from BC municipalities into one normalized relational schema centred on `dev_permit`.

The project covers commercial, residential, mixed-use, industrial, and institutional developments.  Different municipalities publish in very different ways (HTML pages, ArcGIS/VertiGIS webmaps, PDFs), and the objective of this project is to iteratively build harvesting methods for each municipality and consolidate them into one central database.

## Key information locations

* `references/schema.sql`: authoritative database schema and field definitions.
* `.claude/skills/dev-permit-scrapper/SKILL.md`: workflow for adding, modifying, testing, and debugging municipal harvesters.
* `references/`: source-specific implementation guidance. Read only the reference relevant to the current source type.
* `tools/qa/`: browser QA viewer for eyeballing harvested `data/processed/*.json` (run `make qa`).

## Extraction rules

* Parse fields from HTML, tables, prose, and map attributes deterministically whenever possible.
* Use document extraction only when the primary page or map record contains no usable project information.
* Never infer or fabricate an unknown value. Store it as `NULL`.
* Do not use PDF extraction to fill optional missing fields from an otherwise usable page.
* Deterministically extracted values take precedence over model-extracted values.
* Preserve sourcing metadata, including `source_url`, `raw_text`, `extraction_method`, `extraction_confidence`, and `checksum`.
* Do not change `references/schema.sql` unless the task explicitly requires a schema change.

## Project structure
bc_dev_permits/
  features.py -> Reusable deterministic text-to-field parsing
  harvesters/ -> Source and municipality-specific retrieval
  modeling/predict.py -> Local document-to-JSON extraction

docs/
  COUNCIL.md -> Municipality and source registry

references/
  schema.sql
  extraction_prompt.md
  html_detail_pages.md
  arcgis_webmaps.md
  vertigis_webmaps.md
  ollama_pdf_extraction.md

tools/
  qa/ -> Browser QA viewer (index.html + viewer.jsx) for data/processed/*.json

Keep retrieval logic in `harvesters/`. 
Put reusable field parsing and normalization in `features.py`. 
Do not duplicate common parsing logic across municipality modules.

## Development conventions

* Use the repository's existing Conda environment.
* Format and lint Python with Ruff.
* Do not use semicolons or emdashes in comments.
* Keep comments clear and specific, preferring comment blocks over line-by-line, except when the latter adds required clarity.
* Prefer standard-library and existing-project dependencies. Always stop and ask before adding any additional dependencies.
* Keep municipality-specific selectors, field mappings, and endpoints isolated from shared parsing logic.
* Add or update a fixture for each new source structure or parsing rule.
* Run the most focused relevant test first, then the full test suite.
* Never modify source fixtures to make a failing parser test pass.
* Avoid unneeded refactoring when adding or repairing a harvester.

## Data handling

Use public sources only. Rate-limit requests per host, and respect applicable access restrictions.
Cache by checksum and skip unchanged sources on re-harvest. Development-application text can
include personal names (applicants and owners), so decide what to store and surface before
republishing.
