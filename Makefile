.PHONY: help requirements data data-db train plots lint format test docs qa qa-standalone clean

PYTHON := python

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

requirements:  ## Install runtime dependencies
	$(PYTHON) -m pip install -r requirements.txt

data:  ## Harvest all applications -> data/processed/<municipality>.json
	$(PYTHON) -m bc_dev_permits.dataset --limit 0 --out json

data-db:  ## Harvest all applications and upsert into Postgres (needs DATABASE_URL)
	$(PYTHON) -m bc_dev_permits.dataset --limit 0 --out db

train:  ## Train models (placeholder)
	$(PYTHON) -m bc_dev_permits.modeling.train

plots:  ## Generate figures -> reports/figures
	$(PYTHON) -m bc_dev_permits.plots

lint:  ## Lint + import-order + format check with ruff
	ruff check
	ruff format --check

format:  ## Auto-fix and format with ruff
	ruff check --fix
	ruff format

test:  ## Run tests
	$(PYTHON) -m pytest -q

docs:  ## Serve the mkdocs site locally
	mkdocs serve

qa:  ## Serve the JSON QA viewer at http://localhost:8000/tools/qa/
	$(PYTHON) -m http.server 8000

qa-standalone:  ## Bundle the QA viewer + latest Victoria data into one shareable HTML
	$(PYTHON) tools/qa/build_standalone.py data/processed/victoria_remote_machine.json tools/qa/victoria-qa-standalone.html "Victoria Development Permit QA"

clean:  ## Remove Python caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -delete
