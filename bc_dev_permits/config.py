"""Central configuration: project paths + scraper / database / Ollama settings.

Import from here instead of hard-coding constants in modules, e.g.:
    from bc_dev_permits import config
    config.PROCESSED_DATA_DIR / "north_van.json"
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths (Cookiecutter Data Science layout)
# --------------------------------------------------------------------------- #
PROJ_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJ_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
INTERIM_DATA_DIR = DATA_DIR / "interim"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
EXTERNAL_DATA_DIR = DATA_DIR / "external"

MODELS_DIR = PROJ_ROOT / "models"
REPORTS_DIR = PROJ_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"
REFERENCES_DIR = PROJ_ROOT / "references"

# --------------------------------------------------------------------------- #
# HTTP scraping
# --------------------------------------------------------------------------- #
USER_AGENT = os.environ.get(
    "BC_DEV_PERMITS_UA", "bc-dev-permit-scraper/0.1 (+contact: you@example.com)"
)
HTTP_TIMEOUT = 90  # seconds; cnv.org streams the ~500KB index slowly when cold
HTTP_RETRIES = 3  # transient read/connection timeouts are common
HTTP_CACHE_DIR = RAW_DATA_DIR / "http_cache"  # cached raw HTML = raw data
HTTP_CACHE_TTL = 6 * 3600  # reuse a fetched page for 6h so re-runs are instant

# --------------------------------------------------------------------------- #
# Database (Postgres; schema in references/schema.sql)
# --------------------------------------------------------------------------- #
DATABASE_URL = os.environ.get("DATABASE_URL")
SCHEMA_SQL = REFERENCES_DIR / "schema.sql"

# --------------------------------------------------------------------------- #
# Ollama — local LLM fallback for PDFs (bc_dev_permits.modeling.predict)
# --------------------------------------------------------------------------- #
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_TEXT_MODEL = os.environ.get("OLLAMA_TEXT_MODEL", "qwen2.5:7b-instruct")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llama3.2-vision")
EXTRACTION_PROMPT = REFERENCES_DIR / "extraction_prompt.md"

# --------------------------------------------------------------------------- #
# Municipality registry — maps a slug to its harvester's index URL.
# See docs/COUNCIL.md for the full municipality list.
# --------------------------------------------------------------------------- #
NORTH_VAN_LIST_URL = (
    "https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Active-Applications"
)
