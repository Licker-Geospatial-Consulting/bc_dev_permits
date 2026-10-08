"""Shared record consolidation: collapse duplicate records that share one civic address.

Every municipal source can emit several rows for one physical site - an Official Plan Amendment,
a Zoning By-law Amendment and a Subdivision filed together, or the same address published twice
with slightly different punctuation ("125 McDonald Blvd." vs "125 McDonald Blvd"). This module is
the general de-duplication rule a harvester applies before returning its rows: rows at the same
normalized civic address are grouped, the thin administrative records are folded into the strongest
record at that address (their ids / types / statuses kept as ``related_permits`` and their
milestones merged), and genuinely distinct strong applications are left separate.

Harvesters call :func:`consolidate_by_address` at the end of ``harvest()``. It mutates the list in
place and returns how many rows were folded away. Port Moody keeps its own Engage-aware grouping
for project-level consolidation; this module is the address-level rule shared by every source.
"""

from __future__ import annotations

import re

# Strong-signal fields: a row carrying any of these describes a real building program and is kept
# on its own rather than folded into a sibling.
_PROGRAM_FIELDS = ("units_total", "number_of_stories", "floor_area")


def address_key(address: str | None) -> str | None:
    """Return a normalized civic address for grouping, or None when no civic number is present.

    Punctuation and case are normalized so "125 McDonald Blvd." and "125 McDonald Blvd" collapse
    to one key. A civic number is required: a bare "Mill Street" could be any of several lots, so
    such a row is never merged by address.
    """
    norm = re.sub(r"[.,]", " ", (address or "").upper())
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm if norm and re.search(r"\d", norm) else None


def is_strong(row: dict) -> bool:
    """Return True for a record worth keeping on its own: enriched, or with a real program."""
    if (row.get("extraction_method") or "arcgis") != "arcgis":
        return True
    return any(row.get(f) not in (None, "", [], {}) for f in _PROGRAM_FIELDS)


def _program_signature(row: dict):
    """Return a strong record's program signature, or None for a thin record.

    Two strong records at one address that share a signature are the same project filed under
    several permit types (a Subdivision and a Zoning By-law Amendment both stating 1205 units);
    distinct developments at one address have different signatures and stay separate.
    """
    return tuple(row.get(f) for f in _PROGRAM_FIELDS) if is_strong(row) else None


def _dedup_milestones(milestones: list[dict]) -> list[dict]:
    """Drop repeated (milestone, date) pairs while preserving order."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for m in milestones:
        key = (m.get("milestone"), m.get("milestone_date"))
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def _best_record(group: list[dict]) -> dict:
    """Pick the representative record: geocoded first, then the one with the most source text."""
    def key(row: dict) -> tuple:
        has_geom = 1 if row.get("latitude") is not None else 0
        return (has_geom, len(row.get("raw_text") or ""))
    return max(group, key=key)


def merge_related(primary: dict, victims: list[dict]) -> None:
    """Fold sibling records into ``primary`` (keep it geocodable and carry the siblings' identity).

    The victims' identity, type, address, status and milestones are preserved on the primary - as a
    ``related_permits`` list and an appended raw_text block - instead of surviving as separate rows.
    """
    related = [{
        "permit_id": v.get("permit_id"),
        "permit_type": v.get("permit_type"),
        "address": v.get("address"),
        "status": v.get("status"),
    } for v in victims]
    # Append, so a second consolidation pass keeps earlier siblings.
    primary["related_permits"] = (primary.get("related_permits") or []) + related

    # Keep the record geocodable: borrow a sibling's centroid when the primary has none.
    if primary.get("latitude") is None:
        donor = next((v for v in victims if v.get("latitude") is not None), None)
        if donor:
            primary["latitude"] = donor["latitude"]
            primary["longitude"] = donor["longitude"]

    merged = list(primary.get("milestones") or [])
    for v in victims:
        merged.extend(v.get("milestones") or [])
    primary["milestones"] = _dedup_milestones(merged)

    lines = ["Related applications at this address:"]
    for r in related:
        bits = [b for b in (r["permit_id"], r["permit_type"], r["address"], r["status"]) if b]
        lines.append(" - " + " | ".join(bits))
    primary["raw_text"] = "\n\n".join(p for p in (primary.get("raw_text"), "\n".join(lines)) if p)


def consolidate_by_address(rows: list[dict]) -> int:
    """Collapse records that share one civic address into one record per distinct project.

    Two things are folded together at an address:

    * **Thin records** - administrative permits (subdivisions, site plans, minor variances) with no
      building program of their own - are absorbed into the strongest record at that address.
    * **Strong records with an identical program** - the same project filed under several permit
      types (a Subdivision and a Zoning By-law Amendment both stating 1205 units) - are folded into
      one, so the project is not drawn twice.

    Strong records with *different* programs are left separate, so an address with several genuinely
    distinct developments keeps them. A folded record's id / type / status survive as
    ``related_permits`` and its milestones merge in. Mutates ``rows`` in place; returns how many
    rows were folded away.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = address_key(row.get("address"))
        if key:
            groups.setdefault(key, []).append(row)

    dropped: set[int] = set()
    for group in groups.values():
        if len(group) >= 2:
            _consolidate_group(group, dropped)

    if dropped:
        rows[:] = [r for r in rows if id(r) not in dropped]
    return len(dropped)


def _fold(bucket: list[dict], dropped: set[int]) -> None:
    """Merge every record in ``bucket`` into its best member; record the rest in ``dropped``."""
    absorber = _best_record(bucket)
    victims = [r for r in bucket if r is not absorber]
    if victims:
        merge_related(absorber, victims)
        dropped.update(id(r) for r in victims)


def _consolidate_group(group: list[dict], dropped: set[int]) -> None:
    """Fold one address group in place: same-program strong filings, then thin records."""
    # Bucket by program signature: each distinct strong project is one bucket, every thin record
    # shares the None bucket.
    buckets: dict[object, list[dict]] = {}
    for row in group:
        buckets.setdefault(_program_signature(row), []).append(row)
    strong_present = any(sig is not None for sig in buckets)

    # Fold each distinct strong project's duplicate filings (same units/storeys/floor) into one.
    for sig, bucket in buckets.items():
        if sig is not None:
            _fold(bucket, dropped)

    # Fold the thin records into the best record at the address: the strongest surviving project
    # when one exists, otherwise the best thin record (which becomes the address's single survivor).
    thin = buckets.get(None, [])
    if not thin:
        return
    if not strong_present:
        _fold(thin, dropped)  # no project to attach to: thin records merge among themselves
        return
    # Attach the thin records to the strongest surviving project, without merging the distinct
    # strong projects into each other.
    survivors = [r for r in group if _program_signature(r) is not None and id(r) not in dropped]
    absorber = _best_record(survivors)
    merge_related(absorber, thin)
    dropped.update(id(r) for r in thin)
