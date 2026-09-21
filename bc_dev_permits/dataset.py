"""Data acquisition entrypoint (`make data`).

Harvests municipal development-permit applications and either prints them, writes
them to data/processed/<municipality>.json, or upserts them into Postgres.

    python -m bc_dev_permits.dataset --limit 3                 # eyeball 3 rows
    python -m bc_dev_permits.dataset --limit 3 --out json      # -> data/processed/north_van.json
    python -m bc_dev_permits.dataset --out db --dsn postgresql://user:pw@localhost/db

Per-municipality scraping lives in bc_dev_permits.harvesters.*; deterministic field
parsing in bc_dev_permits.features; the PDF fallback in bc_dev_permits.modeling.predict.
"""

from __future__ import annotations

import argparse
import json
import sys

from bc_dev_permits import config
from bc_dev_permits.harvesters import north_van, victoria

# slug -> harvest callable(limit=None, use_cache=True) -> list[dict]
HARVESTERS = {
    "north_van": north_van.harvest,
    "victoria": victoria.harvest,
}

# slug -> optional batch PDF-enricher(rows, use_cache=True) -> count (needs Ollama)
ENRICHERS = {
    "victoria": victoria.enrich_via_pdf,
}


def harvest(
    municipality: str = "north_van", limit: int | None = None, use_cache: bool = True
) -> list[dict]:
    """Harvest one municipality's applications into dev_permit rows."""
    return HARVESTERS[municipality](limit=limit, use_cache=use_cache)


def _run_cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Harvest BC development-permit applications.")
    ap.add_argument(
        "--municipality",
        choices=sorted(HARVESTERS),
        default="north_van",
        help="Which municipal harvester to run (default: north_van).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Process only the first N applications (default 3; 0 = all).",
    )
    ap.add_argument(
        "--out",
        choices=("print", "json", "db"),
        default="print",
        help="Where results go (default: print to stdout).",
    )
    ap.add_argument(
        "--json-path",
        default=None,
        help="Output file for --out json (default: data/processed/<municipality>.json).",
    )
    ap.add_argument(
        "--dsn",
        default=config.DATABASE_URL,
        help="Postgres DSN for --out db (or set DATABASE_URL).",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help=f"Bypass the {config.HTTP_CACHE_TTL // 3600}h page cache and refetch.",
    )
    ap.add_argument(
        "--pdf-enrich",
        action="store_true",
        help="After harvesting, fill low-signal rows from their PDFs via Ollama (slow; "
        "needs a running Ollama server and the [pdf] extra). Supported: "
        f"{', '.join(sorted(ENRICHERS))}.",
    )
    ap.add_argument(
        "--address",
        default=None,
        help="Only process rows whose address or permit id contains this text "
        "(case-insensitive), e.g. --address '23 Cook' or --address DPV00304. Pair with "
        "--limit 0 so the target is not cut off by the harvest limit.",
    )
    args = ap.parse_args(argv)

    rows = harvest(args.municipality, args.limit or None, not args.no_cache)
    print(f"Harvested {len(rows)} {args.municipality} application(s).", file=sys.stderr)

    if args.address:
        needle = args.address.lower()
        rows = [
            r for r in rows
            if needle in (r.get("address") or "").lower()
            or needle in (r.get("permit_id") or "").lower()
        ]
        print(f"Filtered to {len(rows)} row(s) matching '{args.address}'.", file=sys.stderr)

    if args.pdf_enrich:
        enricher = ENRICHERS.get(args.municipality)
        if enricher is None:
            print(f"--pdf-enrich not supported for {args.municipality}; skipping.", file=sys.stderr)
        else:
            print(
                f"[models] text={config.OLLAMA_TEXT_MODEL} vision={config.OLLAMA_VISION_MODEL}",
                file=sys.stderr,
            )
            n = enricher(rows, use_cache=not args.no_cache)
            print(f"PDF-enriched {n} low-signal row(s).", file=sys.stderr)

    if args.out == "json":
        path = args.json_path or (config.PROCESSED_DATA_DIR / f"{args.municipality}.json")
        config.PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
        print(f"Wrote {len(rows)} row(s) -> {path}", file=sys.stderr)
    elif args.out == "db":
        if not args.dsn:
            ap.error("--out db needs --dsn or the DATABASE_URL env var.")
        import psycopg  # pip install "psycopg[binary]"

        from bc_dev_permits import load

        with psycopg.connect(args.dsn) as conn:
            for row in rows:
                load.upsert(conn, row)
        print(f"Upserted {len(rows)} row(s) into Postgres.", file=sys.stderr)
    else:  # print
        json.dump(rows, sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
