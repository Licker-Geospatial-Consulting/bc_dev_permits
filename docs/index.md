# BC Dev Permits

Scraper and extraction pipeline for British Columbia municipal development-permit
applications. It harvests each municipality's active-applications pages, parses the
facts deterministically from the HTML, and falls back to a local Ollama LLM only for
PDF documents. Results land in a normalized Postgres schema (see
[schema.sql](../references/schema.sql)).

## Pipeline

1. **Harvest** - `bc_dev_permits.harvesters.*` fetch each municipality's index and
   detail pages (with a TTL cache in `data/raw/http_cache`).
2. **Parse (deterministic)** - `bc_dev_permits.features` extracts fields from page
   prose with plain regex. No LLM touches HTML.
3. **PDF fallback** - when a page has no usable text, `bc_dev_permits.modeling.predict`
   runs a local Ollama model over the linked PDFs.
4. **Load** - `bc_dev_permits.load` upserts rows into Postgres; `bc_dev_permits.dataset`
   is the `make data` entrypoint that writes JSON or the DB.

## Docs

- [Municipalities (Council)](COUNCIL.md) - the municipality list and source types.
- [QA & Re-harvest Notes](QA_NOTES.md) - per-municipality decisions, gotchas, and re-fetch
  checklists learned during QA (read before re-harvesting new permits).

Project rules and conventions live in `.claude/CLAUDE.md`; the harvesting workflow and field
contract (the Agent Skill) live in `.claude/skills/dev-permit-scrapper/SKILL.md`. See the
top-level `README.md` for the quickstart and full directory layout.
