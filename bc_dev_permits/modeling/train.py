"""Model training entrypoint (`make train`).

Placeholder in the Cookiecutter Data Science layout. This project currently extracts
permit facts deterministically (bc_dev_permits.features) with an Ollama LLM fallback
for PDFs (bc_dev_permits.modeling.predict) — there is no trained model yet. When a
classifier/extractor is trained on the harvested corpus, put that code here and have
it read from data/processed and write artifacts to models/.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for `make train` (no trained model yet)."""
    raise NotImplementedError(
        "No training step yet. Extraction is rule-based (features.py) with an Ollama "
        "PDF fallback (modeling/predict.py).",
    )


if __name__ == "__main__":
    main()
