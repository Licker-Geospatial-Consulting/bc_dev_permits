# BC Dev Permits

Scraper and extraction pipeline for British Columbia municipal **development-permit
applications**. It harvests each municipality's active-applications pages, parses the
facts deterministically from the HTML, and falls back to a local Ollama LLM only for
PDF documents. Results are shaped to a normalized Postgres schema
(`references/schema.sql`).

## Quickstart

```bash
# 1. install (editable, with DB + PDF extras)
pip install -e ".[db,pdf]"          # or: make requirements

# 2. harvest a few applications and eyeball them
python -m bc_dev_permits.dataset --limit 3

# 3. save to data/processed/north_van.json
python -m bc_dev_permits.dataset --limit 3 --out json

# 4. load into Postgres  (needs a running PostgreSQL server — see "Database" below)
createdb devpermits
psql -d devpermits -f references/schema.sql
export DATABASE_URL=postgresql://user:pw@localhost:5432/devpermits
python -m bc_dev_permits.dataset --out db
```

## Database (Postgres) for `--out db`

`--out db` upserts into PostgreSQL and is Postgres-specific (JSONB columns,
`INSERT ... ON CONFLICT`), so it needs a **running PostgreSQL server** — `psycopg`
(the `[db]` extra) is only the client driver. `--out json` / `--out print` need no
database.

```bash
# Install a server (once). Examples:
winget install PostgreSQL.PostgreSQL.17     # Windows; installs the postgresql-x64-17 service + psql
# brew install postgresql@17                # macOS
# docker run -d --name pg -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:17   # any OS

# Then create the DB, load the schema, point DATABASE_URL at it (steps 4 above),
# and upsert. Re-runs are idempotent (upsert on municipality+permit_id).
```

`make data`, `make train`, `make plots`, `make lint`, `make test`, `make qa` wrap the
common commands (run `make help` for the list).

## Pipeline

| Stage | Module | Notes |
|-------|--------|-------|
| Harvest | `bc_dev_permits.harvesters.*` | per-municipality fetch; TTL cache in `data/raw/http_cache` |
| Parse (deterministic) | `bc_dev_permits.features` | plain-regex field extraction — no LLM on HTML |
| PDF fallback | `bc_dev_permits.modeling.predict` | local Ollama over linked PDFs when a page has no text |
| Load | `bc_dev_permits.load` / `bc_dev_permits.dataset` | upsert to Postgres or write JSON |
| Config | `bc_dev_permits.config` | paths + scraper / DB / Ollama settings |

## Project structure

```
├── Makefile           <- Convenience commands: make data, make train, ...
├── README.md
├── data
│   ├── external       <- Data from third party sources.
│   ├── interim        <- Intermediate data that has been transformed.
│   ├── processed      <- Final datasets (harvested permit rows).
│   └── raw            <- Original immutable dump (incl. http_cache/ raw HTML).
├── docs               <- mkdocs project (Home, Council registry).
├── models             <- Trained/serialized models (none yet).
├── notebooks          <- Jupyter notebooks.
├── pyproject.toml     <- Package metadata for bc_dev_permits + black/isort config.
├── references         <- Data dictionary (schema.sql), source notes, extraction prompt.
├── reports
│   └── figures        <- Generated figures.
├── requirements.txt
├── tools
│   └── qa             <- Browser QA viewer for data/processed/*.json (make qa).
├── setup.cfg          <- flake8 config.
└── bc_dev_permits     <- Source package.
    ├── __init__.py
    ├── config.py              <- Paths + configuration.
    ├── dataset.py             <- `make data` entrypoint (harvest -> JSON / DB).
    ├── features.py            <- Deterministic field extraction from prose.
    ├── load.py                <- Row -> parameterized SQL upsert.
    ├── plots.py               <- Visualizations (placeholder).
    ├── harvesters             <- Per-municipality scrapers.
    │   └── north_van.py
    └── modeling
        ├── predict.py         <- Ollama PDF-fallback extractor.
        └── train.py           <- Model training (placeholder).
```

## Notes

- **Deterministic first:** everything visible in a page's HTML is parsed with regex in
  `features.py`. The Ollama model in `modeling/predict.py` is reserved for PDFs on the
  fallback path — no page prose is sent to a model.
- **Caching:** the harvester caches raw HTML under `data/raw/http_cache` for 6h so
  re-runs are instant. `--no-cache` forces a fresh fetch.
- **QA viewer:** `make qa` serves a zero-build browser dashboard (`tools/qa/`) for
  eyeballing harvested output — stats, filters, and per-row parsed-fields-vs-source-prose.
  It auto-loads `data/processed/north_van_test.json` and has a "Load JSON" button for any
  `data/processed/<municipality>.json`. No `make`? Run its one-line command directly from
  the repo root: `python -m http.server 8000`, then open `http://localhost:8000/tools/qa/`.
