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
import contextlib
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
else:
    # MuPDF prints xref-repair warnings ("format error: cannot find object in xref (NNN 0 R)")
    # straight to stderr for slightly malformed PDFs. It rebuilds the xref and opens the file
    # anyway - extraction still works - so silence the noise. A genuinely unreadable PDF still
    # raises on fitz.open(), which callers already handle.
    fitz.TOOLS.mupdf_display_errors(False)  # noqa: FBT003 - third-party positional-bool API

OLLAMA_URL = config.OLLAMA_URL
TEXT_MODEL = config.OLLAMA_TEXT_MODEL  # any solid local instruct model works
VISION_MODEL = config.OLLAMA_VISION_MODEL  # for scanned/image-only PDFs
NUM_CTX = config.OLLAMA_NUM_CTX  # context window; sent explicitly so a small server default
# (some Ollama builds cap at 4096) does not reject a plan-sheet image with HTTP 400
OLLAMA_TIMEOUT = config.OLLAMA_TIMEOUT
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
VISION_DPI = config.OLLAMA_VISION_DPI  # full-sheet render resolution (env-tunable)
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
    "Parking figures are counts of stalls or spaces (small whole numbers), NEVER an area: "
    "never copy a square-foot or square-metre figure (e.g. '4,300 square feet of retail', a "
    "'7,400-square-foot park') into a parking field. "
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
# or hallucinate absurd magnitudes - drop those rather than store a bogus number. Parking
# ceilings are deliberately low: real stall/space counts are a few hundred even for large
# projects, so a 4-digit value is almost always a square-footage misread (517 Chatham read
# "4,300 square feet of retail" and a "7,400-square-foot park" into the parking fields).
_COUNT_LIMITS = {
    "units_total": 10000,
    "number_of_stories": 200,
    "parking_vehicle_stalls": 3000,
    "parking_bike_stalls": 3000,
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


# The schema wants unit_mix as a flat {label: count} map, but the model returns it several
# other ways that all render as "[object Object]" in the QA viewer: a list of objects
# ([{"type": "1-bed", "count": 5}, ...] - 441 Government), or a nested dict grouping by
# category ({"bedrooms": {"3": 10}} - 235 Russell). We flatten all of these to {label: int}.
_UNIT_MIX_LABEL_KEYS = ("type", "unit_type", "bedrooms", "bedroom", "name", "label", "size")
_UNIT_MIX_COUNT_KEYS = ("count", "units", "number", "qty", "quantity", "total")


def _add_unit_mix_item(item: dict, add) -> None:
    """Add one list-item object ({"type":..,"count":..} or bare {label: count}) via `add`."""
    label = next((item[k] for k in _UNIT_MIX_LABEL_KEYS if item.get(k) is not None), None)
    count = next((item[k] for k in _UNIT_MIX_COUNT_KEYS if item.get(k) is not None), None)
    if label is not None and count is not None:
        add(label, count)
    else:  # no label/count keys: treat the object itself as {label: count} pairs
        for key, value in item.items():
            add(key, value)


def _flatten_unit_mix(mix) -> dict | None:
    """Coerce a unit_mix (flat dict, nested dict, or list of objects) to flat {label: int}."""
    flat: dict = {}

    def add(label, value) -> None:
        with contextlib.suppress(TypeError, ValueError):
            flat[str(label)] = int(value)

    if isinstance(mix, dict):
        for key, value in mix.items():
            if isinstance(value, dict):
                # Category group, e.g. {"bedrooms": {"3": 10}} -> {"3 bedrooms": 10}. A bare
                # numeric inner key gets the outer category appended so the label reads.
                for inner, count in value.items():
                    label = f"{inner} {key}".strip() if str(inner).strip().isdigit() else inner
                    add(label, count)
            else:
                add(key, value)
    elif isinstance(mix, list):
        for item in mix:
            if isinstance(item, dict):
                _add_unit_mix_item(item, add)
    return flat or None


def _normalize_unit_mix(fields: dict) -> dict:
    """Flatten a list/nested unit_mix in place; leave a plain {label: int} map unchanged."""
    if not isinstance(fields, dict):
        return fields
    mix = fields.get("unit_mix")
    if isinstance(mix, (list, dict)):
        fields["unit_mix"] = _flatten_unit_mix(mix)
    return fields


def _reconcile_units_total(fields: dict) -> dict:
    """Prefer an itemized unit_mix sum when it exceeds a (mis)read units_total.

    The model sometimes copies a summary count that reads LOW next to its own itemized mix
    (441 Government: "51" stated, but the Junior/2/3-Bedroom lines sum to 52, which the letter
    confirms). An itemized breakdown adding up to MORE than the summary means the summary was
    misread, so the sum wins. A breakdown summing to LESS is likely partial (235 Russell lists
    only the 3-bed subset), so the larger total is kept. Any 'total' line is excluded from the
    sum, and at least two category lines are required, so a lone or summary entry never drives.
    """
    mix = fields.get("unit_mix")
    if not isinstance(mix, dict):
        return fields
    parts = {k: v for k, v in mix.items() if isinstance(v, int) and "total" not in str(k).lower()}
    if len(parts) >= 2 and sum(parts.values()) > (fields.get("units_total") or 0):
        fields["units_total"] = sum(parts.values())
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
            # num_ctx is set explicitly: a plan-sheet image is ~4.5k tokens, which a server
            # defaulting to 4096 rejects with HTTP 400, silently emptying the extraction.
            "options": {"temperature": 0, "num_ctx": NUM_CTX},
        },
        timeout=OLLAMA_TIMEOUT,  # a cold model load can still take a while before generation
    )
    resp.raise_for_status()
    fields = _coerce_counts(json.loads(resp.json()["message"]["content"]))
    return _reconcile_units_total(_normalize_unit_mix(fields))


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


# --------------------------------------------------------------------------- #
# Stat-card geometry backfill (text path)
#
# Consultant "Letter to Mayor and Council" packages summarise the project in a graphic stat
# card - a big number stacked with its label. Text extraction reads the label column and the
# number column as SEPARATE runs, so the linear text loses the label->value adjacency (and the
# card can sit past the text-scan cap entirely). The numbers survive in the word GEOMETRY: each
# value starts in its label's x-column, just below it. We pair them deterministically and
# backfill ONLY the numeric fields the text model missed, so a model value is never overwritten.
# Limited to distinctive labels (not generic "units"/"storeys", which recur in prose and the
# model already reads reliably).
# --------------------------------------------------------------------------- #
_STAT_LABELS = {
    "floor_area": ("floor area",),
    "parking_vehicle_stalls": ("car parking stalls", "vehicle parking stalls"),
    "bike_short_stalls": ("short-term bike stalls", "short term bike stalls"),
    "bike_long_stalls": ("long-term bike stalls", "long term bike stalls"),
}
_STAT_X_TOL = 40.0  # a value must start in its label's x-column (left edges align, points)
_STAT_DY_RANGE = (-8.0, 55.0)  # value sits from just above the label baseline to just below it
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?$")


def _label_spans(words: list) -> list[tuple[str, float, float]]:
    """Group get_text('words') tuples into per-line (phrase, x_left, y_top) spans."""
    from collections import defaultdict

    lines: dict = defaultdict(list)
    for w in words:
        lines[(w[5], w[6])].append(w)  # group by (block, line)
    spans = []
    for ws in lines.values():
        phrase = " ".join(w[4] for w in sorted(ws, key=lambda word: word[0])).lower()
        spans.append((phrase, min(w[0] for w in ws), min(w[1] for w in ws)))
    return spans


def _pair_stats_from_words(words: list) -> dict:
    """Pair each distinctive stat-card label to the number in its column (pure, testable).

    A value token qualifies when it starts in the label's x-column (left edges aligned) and
    sits within the vertical window of a stacked card; the closest such number wins. Numbers
    are returned raw (commas stripped, int when whole); combining/validation is the caller's.
    """
    spans = _label_spans(words)
    numbers = [(w[0], w[1], w[4].replace(",", "")) for w in words if _NUM_RE.match(w[4])]
    dy_min, dy_max = _STAT_DY_RANGE
    out: dict = {}
    for field, labels in _STAT_LABELS.items():
        span = next(((lx, ly) for (phrase, lx, ly) in spans if any(lab in phrase for lab in labels)), None)
        if span is None:
            continue
        lx, ly = span
        best = None
        for nx, ny, ntext in numbers:
            dx, dy = abs(nx - lx), ny - ly
            if dx <= _STAT_X_TOL and dy_min <= dy <= dy_max:
                score = dx + abs(dy)
                if best is None or score < best[0]:
                    best = (score, ntext)
        if best is not None:
            value = float(best[1])
            out[field] = int(value) if value.is_integer() else value
    return out


def _find_stat_page_words(doc) -> list:
    """Return get_text('words') for the page richest in distinctive stat-card labels.

    Scores each page by how many distinct stat fields it names (parking, bike, floor area)
    and returns the best, requiring at least two. A prose page that merely mentions one label
    in a sentence scores 1 and is skipped, so we never backfill from the wrong page.
    """
    best_idx, best_score = None, 0
    for i, page in enumerate(doc):
        low = page.get_text().lower()
        score = sum(any(lab in low for lab in labels) for labels in _STAT_LABELS.values())
        if score > best_score:
            best_idx, best_score = i, score
    return doc[best_idx].get_text("words") if best_idx is not None and best_score >= 2 else []


def _stats_fields(words: list) -> dict:
    """Deterministic permit fields read from a stat card's geometry (pure).

    Returns only the fields the card yields ({floor_area, parking_vehicle_stalls,
    parking_bike_stalls, parking_notes}); the caller decides precedence against other sources.
    """
    if not words:
        return {}
    pairs = _pair_stats_from_words(words)
    out: dict = {}
    if pairs.get("floor_area") is not None:
        out["floor_area"] = pairs["floor_area"]
    if pairs.get("parking_vehicle_stalls") is not None:
        out["parking_vehicle_stalls"] = pairs["parking_vehicle_stalls"]
    short, long_ = pairs.get("bike_short_stalls"), pairs.get("bike_long_stalls")
    if short is not None or long_ is not None:
        out["parking_bike_stalls"] = (short or 0) + (long_ or 0)
        parts = [f"{n} {kind}" for n, kind in ((long_, "long-term"), (short, "short-term")) if n is not None]
        out["parking_notes"] = ", ".join(parts) + " bicycle parking (from summary sheet)"
    return _coerce_counts(out)


def _backfill_stats(fields: dict, words: list) -> dict:
    """Fill numeric stat fields still missing on `fields` from stat-card geometry (never overwrite)."""
    for key, value in _stats_fields(words).items():
        if fields.get(key) is None:
            fields[key] = value
    return fields


# Non-field metadata that rides along on an extraction result and must never be merged as a
# permit column or given a provenance tag.
_PDF_META_KEYS = frozenset(
    {"confidence", "extraction_method", "pdf_source_url", "pdf_document", "occupancies",
     "field_methods", "extraction_confidence", "rental_mix"}
)


def _merge_by_precedence(sources: list[tuple[str, dict]]) -> tuple[dict, dict]:
    """Merge field dicts, highest precedence first; return (merged_fields, {field: method}).

    The first source that supplies a real value for a field wins and stamps that field's
    method, so a deterministic parse always beats a later model guess for the same field.
    """
    from bc_dev_permits.features import has_value

    merged: dict = {}
    methods: dict = {}
    for method, fdict in sources:
        for key, value in (fdict or {}).items():
            if key in _PDF_META_KEYS or key in merged or not has_value(value):
                continue
            merged[key] = value
            methods[key] = method
    return merged, methods


def _tag_all(fields: dict, method: str) -> dict:
    """Stamp every real field on `fields` with `method` under fields['field_methods']."""
    from bc_dev_permits.features import has_value

    fields["field_methods"] = {
        k: method for k, v in fields.items() if k not in _PDF_META_KEYS and has_value(v)
    }
    return fields


def _extract_text_route(text: str, stat_words: list, model: str) -> dict:
    """Text-PDF extraction with per-field provenance: deterministic sources beat the model.

    Precedence: regex over the PDF text (pdf_text_regex) and stat-card geometry (pdf_geometry)
    are deterministic and win; the local text model (pdf_model_text) fills only what is left.
    """
    from bc_dev_permits import features

    regex_fields = features.extract_all(text)  # deterministic rule-based parse of the PDF text
    geom_fields = _stats_fields(stat_words)  # deterministic stat-card geometry
    model_fields = extract_from_text(text, model=model)  # non-deterministic local LLM
    merged, methods = _merge_by_precedence(
        [
            (features.METHOD_PDF_REGEX, regex_fields),
            (features.METHOD_PDF_GEOMETRY, geom_fields),
            (features.METHOD_PDF_MODEL_TEXT, model_fields),
        ]
    )
    merged.setdefault("development_class", model_fields.get("development_class") or "unknown")
    merged.setdefault("occupancy_types", model_fields.get("occupancy_types") or [])
    merged["confidence"] = model_fields.get("confidence")
    merged["field_methods"] = methods
    return merged


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

    # Close the document before returning so the caller can delete a temp file on Windows
    # (an open PyMuPDF handle blocks os.unlink there -> WinError 32).
    with fitz.open(path) as doc:
        long_side = max(doc[0].rect.width, doc[0].rect.height)
        text = ""
        stat_words: list = []
        if long_side <= LARGE_FORMAT_PT:  # letter/report sized -> text if it has real text
            text = "\n\n".join(
                doc[i].get_text() for i in range(min(len(doc), VISION_TEXT_SCAN_PAGES))
            ).strip()
            # Grab the stat-card page's word geometry now (doc closes after this block) so we
            # can backfill numbers the linear text loses - see _backfill_stats.
            stat_words = _find_stat_page_words(doc)

    # Log the route + exact model so a run self-reports whether the text or vision model ran
    # (only large-format/scanned sheets reach the vision model; letters use the text model).
    if len(text) >= MIN_CHARS_PER_PAGE:
        print(f"[predict] {path.name}: text route via {model}", file=sys.stderr)
        return _extract_text_route(text, stat_words, model)
    print(f"[predict] {path.name}: vision route via {vision_model}", file=sys.stderr)
    # A plan set carries no usable text, so every field here is a vision-model read.
    from bc_dev_permits import features

    return _tag_all(extract_from_pdf_vision(path, vision_model), features.METHOD_PDF_MODEL_VISION)


def extract_from_pdf_vision(path: str | Path, vision_model: str = VISION_MODEL) -> dict:
    """Run a few FOCUSED full-sheet passes over the first sheets and merge the fields.

    Each pass sends the whole sheet with a prompt scoped to a few fields (see VISION_PASSES),
    which a capable VLM reads far more accurately than a crowded all-fields request; the
    passes then merge by majority vote so each contributes the cells it read cleanly.
    """
    results: list[dict] = []
    # Close the document before returning so the caller can delete a temp file on Windows.
    with fitz.open(path) as doc:
        for pno in range(min(len(doc), VISION_MAX_PAGES)):
            image = _page_b64(doc[pno], VISION_DPI)
            for prompt in VISION_PASSES:
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt, "images": [image]},
                ]
                try:
                    results.append(_chat(messages, vision_model))
                except (requests.RequestException, ValueError) as exc:
                    # Say WHY a pass failed (timeout, HTTP 500/OOM, bad JSON) instead of
                    # silently swallowing it - this is what makes a whole sheet "return empty".
                    print(
                        f"[predict] vision pass failed on page {pno}: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    continue

    fields = _reduce_fields(results)
    _finalize_parking(fields)
    _reconcile_units_total(fields)  # itemized mix beats a low units_total (see the text path)
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
