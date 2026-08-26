#!/usr/bin/env python3
"""Local Ollama field extractor for the dev-permit scraper — PDFs ONLY.

HTML pages and map attributes are parsed deterministically by bc_dev_permits.features
and never reach a model. This module is the fallback path for PDF documents.

Entry points (JSON contract in references/extraction_prompt.md):
  * extract_from_pdf(path)   -> dict   # PRIMARY: text PDFs + scanned/vision PDFs
  * extract_from_text(text)  -> dict   # helper for text ALREADY pulled from a PDF
                                       # (not for HTML page prose — use features.py)

Design (all via PyMuPDF - fast even on 40-page, 77 MB plan sets):
  1. Route by page size. Standard-size pages (letters/reports) -> pull their text and send
     it to a local text model.
  2. Large-format architectural sheets (plan sets) hold their data in image/vector tables
     that text extraction can't read, so rasterize the sheet and send it to a local vision
     model (qwen2.5-VL) - see extract_from_pdf_vision.
  3. Output is requested in JSON mode and parsed (a full schema grammar hangs on long input).

Everything runs against a local Ollama server (default http://localhost:11434);
no data leaves the machine. Ollama must be running and the models pulled, e.g.:
    ollama pull qwen2.5:7b-instruct
    ollama pull qwen2.5vl:7b           # vision, for image-only / plan-set PDFs

Deps:  pip install requests pymupdf
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import sys

import requests

from bc_dev_permits import config

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

OLLAMA_URL = config.OLLAMA_URL
TEXT_MODEL = config.OLLAMA_TEXT_MODEL  # any solid local instruct model works
VISION_MODEL = config.OLLAMA_VISION_MODEL  # for scanned/image-only PDFs
MIN_CHARS_PER_PAGE = 40  # below this, treat the page as scanned

# Image-only PDFs (e.g. architectural plan sets) carry their facts in a small
# project-data table on a large-format sheet. Sending the whole sheet to a vision model
# fails - the table downscales to unreadable and the model hallucinates. Instead we sweep
# each sheet with overlapping high-DPI windows, OCR each, and merge by majority vote.
#
# A SLIDING WINDOW (not a fixed grid) is used so every table row appears WHOLE in at least
# one window: the vertical step is smaller than the window height, so no boundary can
# bisect a row (a fixed grid could split "PARKING STALLS 9+1 VISITOR" and lose it). Window
# and step are fractions of the page. See references/ollama_pdf_extraction.md and
# tests/test_predict_vision.py.
# A capable VLM (qwen2.5-VL) reads a whole high-resolution sheet WITH context in one look,
# so we do NOT crop (spatial tiling cut tables apart and the merge picked noise). We run
# one comprehensive full-sheet pass per entry in VISION_PASSES and merge them. On a strong
# model a SINGLE pass is most accurate (splitting fields across passes made it worse), so
# the default is one entry; for a weaker model you can list several focused prompts and
# they merge by majority vote.
VISION_DPI = 220  # full-sheet render resolution
VISION_MAX_PAGES = 1  # the project-data table is on the cover sheet; raise to sweep more
VISION_TEXT_SCAN_PAGES = 8  # cap the text-probe scan (plan sets are long)
# Long side (points) above which a page is a large-format architectural sheet, whose data
# lives in image/vector tables the text extractor can't read (~11 in letter, ~39 in plans).
LARGE_FORMAT_PT = 1500
VISION_PASSES = (
    "Read the PROJECT DATA / statistics table on this architectural sheet and extract the "
    "development-permit fields: total dwelling units (units_total), the unit mix by bedroom "
    "count (unit_mix), number of storeys, the residential and commercial floor areas "
    "(floor_area), use (development_class), tenure (rental_or_strata), and density "
    "(zoning_density). For parking use the PROVIDED counts, never the required/bylaw "
    "minimums: parking_vehicle_stalls provided, and parking_bike_stalls = total bicycle "
    "spaces provided (long-term + short-term) with the breakdown in parking_notes. Copy "
    "numbers exactly; null anything not shown.",
)

SYSTEM_PROMPT = (
    "You extract development-permit facts from municipal text into the given JSON schema. "
    "Fill EVERY field whose value appears anywhere in the text; use null ONLY when the "
    "value is genuinely absent. Never guess or infer beyond what is written. A drawing's "
    "data table often lists a REQUIRED (or minimum/bylaw) figure next to a PROVIDED (or "
    "proposed/supplied) figure - ALWAYS take the PROVIDED figure, never the required one "
    "(e.g. parking provided, not parking required). Copy numbers "
    "exactly, and convert worded numbers to integers (e.g. 'six-storey' -> 6, 'nine "
    "units' -> 9). When a count is split into sub-totals (e.g. '108 long-term and 8 "
    "short-term bicycle parking spaces', or '54 residential and 7 visitor stalls'), record "
    "the COMBINED total in the numeric field (parking_bike_stalls -> 116, "
    "parking_vehicle_stalls -> 61) and keep the breakdown in the matching notes field. "
    "development_class must be exactly one of: residential, commercial, "
    "mixed, industrial, institutional, unknown (use 'mixed' when residential and "
    "non-residential uses are combined). List every distinct use in occupancy_types. "
    "Provide a confidence from 0 to 1 reflecting how completely the text supported the fields."
)

# JSON schema handed to Ollama's `format` param — mirrors references/extraction_prompt.md
SCHEMA = {
    "type": "object",
    "properties": {
        "development_name": {"type": ["string", "null"]},
        "address": {"type": ["string", "null"]},
        "permit_type": {"type": ["string", "null"]},
        "development_class": {
            "type": "string",
            "enum": ["residential", "commercial", "mixed", "industrial", "institutional", "unknown"],
        },
        "status": {"type": ["string", "null"]},
        "floor_area": {"type": ["number", "null"]},
        "footprint_area": {"type": ["number", "null"]},
        "number_of_stories": {"type": ["integer", "null"]},
        "units_total": {"type": ["integer", "null"]},
        "unit_mix": {"type": ["object", "null"]},
        "occupancy_types": {"type": "array", "items": {"type": "string"}},
        "rental_or_strata": {"type": ["string", "null"]},
        "rental_subtype": {"type": ["string", "null"]},
        "parking_vehicle_stalls": {"type": ["integer", "null"]},
        "parking_bike_stalls": {"type": ["integer", "null"]},
        "parking_notes": {"type": ["string", "null"]},
        "building_materials": {"type": ["string", "null"]},
        "retrofit_info": {"type": ["string", "null"]},
        "zoning_density": {"type": ["string", "null"]},
        "energy_info": {"type": ["string", "null"]},
        "mechanical_system_info": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["development_class", "occupancy_types", "confidence"],
}


# We use Ollama's lightweight JSON mode (format="json") rather than a full schema grammar.
# The schema-constrained decoder stalls pathologically on long prompts (a ~4k-token letter
# hung past 600s on a warm GPU, where free-form / json-mode finish in ~9s and extract MORE
# fields). The field list is given to the model in the prompt instead (see SYSTEM_PROMPT),
# and outputs are tolerated loosely downstream.
SYSTEM_PROMPT += (
    " Return a JSON object using ONLY these keys (null when a value is not stated): "
    + ", ".join(SCHEMA["properties"])
    + "."
)


# Count fields must be whole, non-negative, and within a sane ceiling. Models often misread
# a dimension or area cell in a drawing table as a count (e.g. parking "27.62", bike "12.21")
# or hallucinate absurd magnitudes - drop those rather than store a bogus number.
_COUNT_LIMITS = {
    "units_total": 100000,
    "number_of_stories": 200,
    "parking_vehicle_stalls": 100000,
    "parking_bike_stalls": 100000,
    "footprint_area": None,  # numeric, not a count -> left as-is
}


def _coerce_counts(fields: dict) -> dict:
    """Null out count fields that are not whole, non-negative, in-range integers."""
    if not isinstance(fields, dict):
        return {}
    for key, ceiling in _COUNT_LIMITS.items():
        if ceiling is None or fields.get(key) is None:
            continue
        try:
            value = float(fields[key])
        except (TypeError, ValueError):
            fields[key] = None
            continue
        fields[key] = int(value) if value.is_integer() and 0 < value <= ceiling else None
    return fields


def _chat(messages: list[dict], model: str) -> dict:
    """Call Ollama /api/chat in JSON mode, parse the object, and coerce count fields."""
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": messages,
            "format": "json",  # lightweight JSON mode (a full schema grammar hangs on long input)
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=600,  # a cold model load can still take a while before generation starts
    )
    resp.raise_for_status()
    return _coerce_counts(json.loads(resp.json()["message"]["content"]))


def extract_from_text(text: str, model: str = TEXT_MODEL) -> dict:
    """Extract permit fields from a block of prose or PDF-derived text."""
    text = (text or "").strip()
    if not text:
        return {"development_class": "unknown", "occupancy_types": [], "confidence": 0.0}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Extract the development-permit fields from the text below. Look carefully "
                "for: total dwelling/unit count, number of storeys, parking stalls, bicycle "
                "parking, tenure (rental vs strata), floor area, and density (FSR/FAR).\n\n"
                f"{text}"
            ),
        },
    ]
    return _chat(messages, model)


# Merge precedence: prefer a real classification over the 'unknown' sentinel.
_EMPTY = (None, "", "unspecified", "not available", "unknown", [], {})


def _page_b64(page, dpi: int) -> str:
    """Render a whole PDF page to a base64 PNG for the vision model."""
    return base64.b64encode(page.get_pixmap(dpi=dpi).tobytes("png")).decode()


def _sum_ints(pattern: str, text: str) -> int | None:
    """Sum the two integers captured by `pattern` in `text`, else None."""
    m = re.search(pattern, text or "", re.IGNORECASE)
    return int(m.group(1)) + int(m.group(2)) if m else None


def _finalize_parking(fields: dict) -> None:
    """Derive stall totals from the verbatim parking_notes the vision model copied.

    The model reliably transcribes cells like '9+1 VISITOR' and '7 SHORT TERM : 18 LONG
    TERM' but does the arithmetic inconsistently, so we sum them deterministically here.
    """
    notes = fields.get("parking_notes") or ""
    vehicle = _sum_ints(r"(\d+)\s*\+\s*(\d+)", notes)  # "9+1 VISITOR" -> 10
    if vehicle is not None:
        fields["parking_vehicle_stalls"] = vehicle
    # long/short-term bike counts, tolerating words/punctuation between them, e.g.
    # "182 Long Term Bicycle Parking, 14 Short Term" or "108 long-term and 8 short-term".
    bike = _sum_ints(r"(\d+)\s*long[\s-]*term\D{0,30}?(\d+)\s*short", notes) or _sum_ints(
        r"(\d+)\s*short[\s-]*term\D{0,30}?(\d+)\s*long", notes
    )
    if bike is not None:
        fields["parking_bike_stalls"] = bike


def _majority(vals: list):
    """Most common value; ties fall to first-seen. Unhashable (dict/list) -> first value."""
    from collections import Counter

    try:
        return Counter(vals).most_common(1)[0][0]
    except TypeError:  # a model returned an unhashable object for a normally-scalar field
        return vals[0]


def _reduce_fields(tiles: list[dict]) -> dict:
    """Merge many tile extractions into one record, robust to one-off OCR misreads.

    Scalars are decided by MAJORITY VOTE across the tiles that reported them (so a value
    several tiles agree on, e.g. floor_area 1321, beats a lone misread like 186.62; ties
    fall to first-seen). unit_mix dicts union, occupancy_types union, and parking_notes
    keeps the most complete transcription.
    """
    collected: dict[str, list] = {}
    for tile in tiles:
        for key, val in (tile or {}).items():
            if val not in _EMPTY:
                collected.setdefault(key, []).append(val)

    out: dict = {}
    for key, vals in collected.items():
        if key == "unit_mix":
            mix: dict = {}
            for v in vals:
                if isinstance(v, dict):
                    mix.update(v)
            out[key] = mix or None
        elif key == "occupancy_types":
            out[key] = sorted({x for v in vals for x in (v if isinstance(v, list) else [])})
        elif key == "parking_notes":
            out[key] = max(vals, key=lambda s: len(str(s)))
        else:
            out[key] = _majority(vals)
    return out


def extract_from_pdf(
    path: str | Path, model: str = TEXT_MODEL, vision_model: str = VISION_MODEL
) -> dict:
    """Extract from a PDF, routing by page size (fast PyMuPDF probe, no slow pdfplumber).

    Standard-size documents (letters/reports) hold their facts as prose the text model can
    read. Large-format architectural sheets (plan sets) carry their data in image/vector
    tables that text extraction cannot read - even though they contain lots of *other* text
    (street names, notes) - so they go to the vision model regardless of text volume.
    """
    path = Path(path)
    if fitz is None:
        raise RuntimeError("Reading PDFs needs PyMuPDF: pip install pymupdf")

    doc = fitz.open(path)
    long_side = max(doc[0].rect.width, doc[0].rect.height)
    if long_side <= LARGE_FORMAT_PT:  # letter/report sized -> text model if it has real text
        text = "\n\n".join(
            doc[i].get_text() for i in range(min(len(doc), VISION_TEXT_SCAN_PAGES))
        ).strip()
        if len(text) >= MIN_CHARS_PER_PAGE:
            return extract_from_text(text, model=model)

    # Large-format sheet, or a page-sized scan with no extractable text -> vision.
    return extract_from_pdf_vision(path, vision_model)


def extract_from_pdf_vision(path: str | Path, vision_model: str = VISION_MODEL) -> dict:
    """Run a few FOCUSED full-sheet passes over the first sheets and merge the fields.

    Each pass sends the whole sheet with a prompt scoped to a few fields (see VISION_PASSES),
    which a capable VLM reads far more accurately than a crowded all-fields request; the
    passes then merge by majority vote so each contributes the cells it read cleanly.
    """
    doc = fitz.open(path)
    results: list[dict] = []
    for pno in range(min(len(doc), VISION_MAX_PAGES)):
        image = _page_b64(doc[pno], VISION_DPI)
        for prompt in VISION_PASSES:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt, "images": [image]},
            ]
            try:
                results.append(_chat(messages, vision_model))
            except (requests.RequestException, ValueError):
                continue  # a bad/empty pass should not abort the sheet

    fields = _reduce_fields(results)
    _finalize_parking(fields)
    fields.setdefault("development_class", "unknown")
    fields.setdefault("occupancy_types", [])
    # Vision reads of dense drawings are best-effort and can still hallucinate a confident
    # but wrong cell. Cap confidence below the review threshold so these rows are always
    # human-checked and never trusted as final numbers, whatever the model claims.
    fields["confidence"] = round(min(fields.get("confidence") or 0.0, 0.4), 2)
    return fields


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "usage: python -m bc_dev_permits.modeling.predict "
            "<file.pdf | -  (read text from stdin)>"
        )
        raise SystemExit(1)
    arg = sys.argv[1]
    result = sys.stdin.read() if arg == "-" else None
    out = extract_from_text(result) if result is not None else extract_from_pdf(arg)
    print(json.dumps(out, indent=2))
