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
# qwen2.5-VL reads dense project-data tables on architectural sheets far better than
# minicpm-v (which hallucinated round numbers): on 1708 Quadra it read the real unit mix,
# storeys, floor area, and use from the FULL sheet in one call. minicpm-v / llava were
# tried and are weaker; llama3.2-vision's 'mllama' architecture won't load on this build.
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
# Context window sent with every request. Ollama's per-server default varies (some builds cap
# at 4096), and a rendered plan sheet tokenizes to ~4.5k image tokens - which a 4096 window
# rejects with HTTP 400 ("exceeds the available context size"), silently emptying the result.
# Setting it explicitly makes machines behave the same regardless of their server default.
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "600"))  # seconds per request
# Render resolution for the vision path. Lower it (e.g. 150) to cut image tokens and memory on
# a constrained GPU; raise it for sharper tables if the model has context/VRAM to spare.
OLLAMA_VISION_DPI = int(os.environ.get("OLLAMA_VISION_DPI", "220"))
EXTRACTION_PROMPT = REFERENCES_DIR / "extraction_prompt.md"

# --------------------------------------------------------------------------- #
# Municipality registry — maps a slug to its harvester's index URL.
# See docs/COUNCIL.md for the full municipality list.
# --------------------------------------------------------------------------- #
NORTH_VAN_LIST_URL = (
    "https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Active-Applications"
)

# Victoria publishes through the Prospero "OurCity" permit tracker (see
# references/prospero_tracker.md). The search page defaults to the ACTIVE set; detail
# pages are plain GETs keyed by folder number.
VICTORIA_LIST_URL = "https://tender.victoria.ca/webapps/ourcity/prospero/search.aspx"
VICTORIA_DETAIL_BASE = "https://tender.victoria.ca/webapps/ourcity/Prospero/Details.aspx"

# Coquitlam publishes through an ArcGIS Experience Builder app (see
# references/arcgis_webmaps.md). The app is just a UI over one FeatureServer layer, so the
# harvester queries the service directly rather than scraping the map. COQUITLAM_SERVICE_URL
# is that layer; COQUITLAM_APP_URL is the human-facing page recorded as each row's source_url.
COQUITLAM_APP_URL = (
    "https://experience.arcgis.com/experience/346bbb6ec0024422abcf27b0e338dbad/page/Page?org=Coquitlam"
)
COQUITLAM_SERVICE_URL = (
    "https://services2.arcgis.com/Q6Lq3evZUGfPrN7o/arcgis/rest/services/"
    "Development_Information_Demo/FeatureServer/0"
)

# Maple Ridge publishes through a VertiGIS Studio Web app (see references/vertigis_webmaps.md),
# which is a UI over one ArcGIS MapServer. The harvester queries the service directly:
# layer 0 DevelopmentApplications (polygons) joined to table 2 ApplicationDetails by FolderRSN.
MAPLE_RIDGE_APP_URL = "https://apps.vertigisstudio.com/web/?app=8b409970fec048b0940b60fe1e225e39"
MAPLE_RIDGE_SERVICE_URL = (
    "https://geoservices.mapleridge.ca/server/rest/services/"
    "LandDevelopmentViewer/LandDevelopmentData/MapServer"
)
