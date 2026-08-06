"""Visualization helpers (`make plots`).

Placeholder in the Cookiecutter Data Science layout. Read harvested rows from
data/processed and write figures to reports/figures. Example directions once there is
data to chart: permits per municipality, unit counts over time, rental vs. strata mix,
share of applications still needing PDF extraction (needs_pdf_extraction).
"""

from __future__ import annotations

from bc_dev_permits import config


def main() -> None:
    """Entry point for `make plots` (no figures defined yet)."""
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    raise NotImplementedError(
        f"No plots defined yet. Save figures to {config.FIGURES_DIR}.",
    )


if __name__ == "__main__":
    main()
