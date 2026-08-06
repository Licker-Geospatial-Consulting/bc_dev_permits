"""
Map a parsed harvester row to parameterized SQL for the schema in resources/schema.sql.

`build_statements(row)` is a PURE function returning a list of (sql, params) tuples, so
it's unit-testable without a database. `upsert(conn, row)` runs them against a live
psycopg connection (Postgres). Keeping the two separate lets tests verify the mapping
deterministically.

Child rows (milestones, documents, occupancy) reference the permit via
(municipality, permit_id) rather than a surrogate id, so this stays connection-agnostic;
the FKs resolve through a sub-select on upsert.
"""
from __future__ import annotations
import json

_PERMIT_COLS = [
    "municipality", "permit_id", "source_url", "development_name", "address",
    "latitude", "longitude", "permit_type", "development_class", "status",
    "floor_area", "footprint_area", "number_of_stories", "units_total", "unit_mix",
    "rental_or_strata", "rental_subtype", "rental_mix", "parking_vehicle_stalls",
    "parking_bike_stalls", "parking_notes", "building_materials", "retrofit_info",
    "zoning_density", "energy_info", "mechanical_system_info", "raw_text",
    "is_parsed", "needs_pdf_extraction", "needs_review", "extraction_method",
    "extraction_confidence",
]
_JSON_COLS = {"unit_mix", "rental_mix"}


def _permit_values(row: dict) -> list:
    vals = []
    for c in _PERMIT_COLS:
        v = row.get(c)
        vals.append(json.dumps(v) if c in _JSON_COLS and v is not None else v)
    return vals


def build_statements(row: dict) -> list[tuple[str, list]]:
    """Return ordered (sql, params) tuples that upsert the permit and its children."""
    stmts: list[tuple[str, list]] = []

    placeholders = ", ".join(["%s"] * len(_PERMIT_COLS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _PERMIT_COLS
                        if c not in ("municipality", "permit_id"))
    stmts.append((
        f"INSERT INTO dev_permit ({', '.join(_PERMIT_COLS)}) VALUES ({placeholders}) "
        f"ON CONFLICT (municipality, permit_id) DO UPDATE SET {updates}, "
        f"last_updated = now();",
        _permit_values(row),
    ))

    pid = (row["municipality"], row["permit_id"])
    sub = ("(SELECT id FROM dev_permit WHERE municipality = %s AND permit_id = %s)")

    # Replace children so re-harvest stays idempotent.
    for child in ("dev_permit_milestone", "dev_permit_occupancy"):
        stmts.append((f"DELETE FROM {child} WHERE permit_id = {sub};", list(pid)))

    for m in row.get("milestones", []):
        stmts.append((
            f"INSERT INTO dev_permit_milestone (permit_id, milestone, milestone_date) "
            f"VALUES ({sub}, %s, %s);",
            [*pid, m["milestone"], m.get("milestone_date")],
        ))

    # Prefer structured occupancies (carry per-use detail/floor_area); fall back to the
    # plain string list for rows parsed before that field existed / the PDF-fallback path.
    occupancies = row.get("occupancies") or [{"occupancy": o} for o in row.get("occupancy_types", [])]
    for occ in occupancies:
        stmts.append((
            f"INSERT INTO dev_permit_occupancy "
            f"(permit_id, occupancy, detail, floor_area, floor_area_unit) "
            f"VALUES ({sub}, %s, %s, %s, %s);",
            [*pid, occ["occupancy"], occ.get("detail"),
             occ.get("floor_area"), occ.get("floor_area_unit")],
        ))

    for d in row.get("documents", []):
        stmts.append((
            f"INSERT INTO dev_document (permit_id, municipality, url, title, doc_role) "
            f"VALUES ({sub}, %s, %s, %s, %s) ON CONFLICT (municipality, url) DO NOTHING;",
            [*pid, row["municipality"], d["url"], d.get("title"), d.get("doc_role")],
        ))

    return stmts


def upsert(conn, row: dict) -> None:
    """Execute the upsert against a live psycopg connection."""
    with conn.cursor() as cur:
        for sql, params in build_statements(row):
            cur.execute(sql, params)
    conn.commit()