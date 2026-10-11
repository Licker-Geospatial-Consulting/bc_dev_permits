"""Shared PDF-enrichment plumbing for the HTML harvesters (Victoria, North Vancouver, ...).

When a detail page gives us little to go on, we read the application's best PDF through the
local Ollama extractor (bc_dev_permits.modeling.predict) and fill the row's gaps. The rules
for WHEN to do this, HOW to merge a model result without clobbering deterministic HTML, and
HOW to record provenance / conflicts are identical across municipalities - only the choice of
WHICH document and HOW to fetch it differ, so those stay in each harvester.

Precedence (see .claude/CLAUDE.md): deterministic HTML values are never overwritten by a model
read; a genuine HTML-vs-PDF disagreement is recorded in row['conflicts'] rather than dropped;
and needs_review is driven by trust (a non-deterministic field or an open conflict), not by the
mere fact that a PDF was consulted.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from bc_dev_permits import features

if TYPE_CHECKING:
    from collections.abc import Callable

# Fields carried on a predict result that are metadata, not permit columns.
PDF_META = {
    "confidence", "extraction_method", "pdf_source_url", "pdf_document", "occupancies",
    "field_methods",
}

# The substantive project-detail fields. Address, permit_type, and development_class are page
# metadata present on nearly every record, so a page can score ~0.6 while telling us nothing
# about the actual development. A row holding NONE of these is a PDF-enrichment candidate
# regardless of its score. See features.has_value for what counts.
SUBSTANCE_FIELDS = ("units_total", "number_of_stories", "floor_area")


def needs_pdf(row: dict) -> bool:
    """Return True when the page gave us little to go on (a PDF-enrichment candidate).

    A row qualifies when it is explicitly flagged, when its confidence is low, or when it
    carries no substantive project detail at all - the last case catches metadata-only pages
    whose ever-present address/type/class float the score above the confidence threshold.
    """
    if bool(row.get("needs_pdf_extraction")) or (row.get("extraction_confidence") or 0) <= 0.4:
        return True
    return not any(features.has_value(row.get(field)) for field in SUBSTANCE_FIELDS)


def values_conflict(a, b) -> bool:
    """Report whether two scalar values genuinely disagree (numeric- and case-insensitive)."""
    if a == b:
        return False
    try:
        return float(str(a).replace(",", "")) != float(str(b).replace(",", ""))
    except (TypeError, ValueError):
        pass
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() != b.strip().lower()
    return True


def tag_existing_html(row: dict) -> dict:
    """Stamp the row's pre-PDF content fields as deterministic HTML provenance."""
    methods = row.setdefault("field_methods", {})
    for key in features.CONTENT_FIELDS:
        if key not in methods and features.has_value(row.get(key)):
            methods[key] = features.METHOD_HTML
    return methods


def merge_pdf_fields(row: dict, fields: dict) -> int:
    """Fill the row's MISSING permit fields from the PDF result; record provenance + conflicts.

    Deterministic HTML values already on the row are never overwritten (deterministic beats
    model). Each filled field is stamped with the method predict reported (regex / geometry /
    model), and any real HTML-vs-PDF disagreement is logged to row['conflicts'] rather than
    silently dropped. Returns the count of fields filled.
    """
    pdf_methods = fields.get("field_methods") or {}
    row_methods = tag_existing_html(row)
    conflicts = row.setdefault("conflicts", [])
    filled = 0
    for key, val in (fields or {}).items():
        if key in PDF_META or not features.has_value(val):
            continue
        method = pdf_methods.get(key, features.METHOD_PDF_MODEL_TEXT)
        if features.has_value(row.get(key)):
            # Keep the deterministic HTML value, but surface a genuine scalar disagreement.
            if isinstance(val, (int, float, str)) and values_conflict(row[key], val):
                conflicts.append({
                    "field": key,
                    "kept": {"value": row[key], "method": row_methods.get(key, features.METHOD_HTML)},
                    "pdf": {"value": val, "method": method},
                })
            continue
        row[key] = val
        row_methods[key] = method
        filled += 1
    return filled


def record_pdf_provenance(row: dict, fields: dict) -> None:
    """Note the PDF source, mark its document extracted, and set review from provenance.

    needs_review is driven by trust, not by "is it a PDF": a row is flagged only when it holds
    a non-deterministic (model) field or an unresolved HTML-vs-PDF conflict. A row filled purely
    by deterministic parses (regex / geometry) is left unflagged.
    """
    row["extraction_method"] = f"html+{fields.get('extraction_method', 'ollama')}"
    used = set((row.get("field_methods") or {}).values())
    row["needs_review"] = bool(used & features.NONDETERMINISTIC_METHODS) or bool(row.get("conflicts"))
    row["extraction_confidence"] = features.score_confidence(row)
    doc = fields.get("pdf_document") or {}
    if doc.get("url"):
        docs = row.setdefault("documents", [])
        existing = next((d for d in docs if d.get("url") == doc["url"]), None)
        if existing:
            existing["extracted"] = True
        else:
            docs.append({"url": doc["url"], "title": doc.get("title"),
                         "doc_role": "ollama_source", "extracted": True})


def apply_claude_pdf(row: dict, fields: dict, document: dict | None = None) -> int:
    """Merge Claude-read PDF fields into a row; record provenance + conflicts. Return count filled.

    Shared by the Claude-read harvesters (West Vancouver, UBC): ``fields`` is the schema-shaped dict
    Claude extracted from the PDF (only stated values). Each is stamped method ``pdf_claude``
    (non-deterministic), deterministic HTML values are never overwritten, and a genuine HTML-vs-PDF
    disagreement is logged to ``row['conflicts']`` - all via the shared merge code, the same way
    Ollama results are handled.
    """
    # A low-signal HTML row carries a placeholder development_class="unknown" / empty occupancy;
    # clear those so the PDF's concrete class fills the gap instead of logging a false conflict.
    if row.get("development_class") in (None, "unknown"):
        row["development_class"] = None
    if not row.get("occupancy_types"):
        row.pop("occupancy_types", None)

    payload = {k: v for k, v in (fields or {}).items()
               if k not in PDF_META and features.has_value(v)}
    payload["field_methods"] = dict.fromkeys(payload, features.METHOD_PDF_CLAUDE)
    payload["extraction_method"] = "claude_read"
    if document:
        payload["pdf_document"] = document
    if "occupancies" in (fields or {}):
        payload["occupancies"] = fields["occupancies"]
    filled = merge_pdf_fields(row, payload)
    if filled or row.get("conflicts"):
        record_pdf_provenance(row, payload)
        if payload.get("occupancies"):
            row["occupancies"] = payload["occupancies"]
    return filled


def enrich(rows: list[dict], *, fetch_html: Callable[[str, bool], str],  # noqa: PLR0913
           extract: Callable[[str, str, object], dict | None], session=None,
           use_cache: bool = True, limit: int | None = None, label: str = "pdf") -> int:
    """Fill each low-signal row from its best PDF via Ollama (in place). Return count enriched.

    `fetch_html(url, use_cache) -> html` and `extract(html, url, session) -> fields|None` are
    supplied by the harvester (they own document selection and fetching). Network / Ollama /
    PDF errors on one row are logged and skipped so the batch keeps going.
    """
    enriched = 0
    for row in rows:
        if not needs_pdf(row) or (limit and enriched >= limit):
            continue
        try:
            fields = extract(fetch_html(row["source_url"], use_cache), row["source_url"], session)
        except (OSError, ValueError, RuntimeError) as exc:  # network / pdf / ollama
            print(f"{label}: pdf-enrich skipped {row.get('permit_id')}: {exc}", file=sys.stderr)
            continue
        if not fields:
            print(f"{label}: no PDF document found for {row.get('permit_id')}; left html-only "
                  "(try --no-cache if the detail page looks stale)", file=sys.stderr)
            continue
        # Record provenance when the PDF filled a gap OR only disagreed with the page (a conflict
        # is still a reviewable result even if nothing new was written).
        if merge_pdf_fields(row, fields) or row.get("conflicts"):
            record_pdf_provenance(row, fields)
            enriched += 1
        else:
            print(f"{label}: PDF for {row.get('permit_id')} yielded no usable fields "
                  "(model returned empty); left html-only", file=sys.stderr)
    return enriched
