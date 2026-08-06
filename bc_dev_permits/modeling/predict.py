#!/usr/bin/env python3
"""Local Ollama field extractor for the dev-permit scraper — PDFs ONLY.

HTML pages and map attributes are parsed deterministically by bc_dev_permits.features
and never reach a model. This module is the fallback path for PDF documents.

Entry points (JSON contract in references/extraction_prompt.md):
  * extract_from_pdf(path)   -> dict   # PRIMARY: text PDFs + scanned/vision PDFs
  * extract_from_text(text)  -> dict   # helper for text ALREADY pulled from a PDF
                                       # (not for HTML page prose — use features.py)

Design:
  1. Pull text with pdfplumber (fast, deterministic).
  2. If a page has little/no text (scanned), rasterize it with PyMuPDF and send the
     image to a local vision model (llava / llama3.2-vision) instead.
  3. Send text to a local text model (qwen2.5 / llama3.1) with a JSON schema so
     Ollama constrains the output to valid JSON (`format=<schema>`).

Everything runs against a local Ollama server (default http://localhost:11434);
no data leaves the machine. Ollama must be running and the models pulled, e.g.:
    ollama pull qwen2.5:7b-instruct
    ollama pull llama3.2-vision        # only if you need scanned-PDF OCR

Deps:  pip install requests pdfplumber pymupdf
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import sys

import requests

from bc_dev_permits import config

try:
    import pdfplumber
except ImportError:
    pdfplumber = None
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

OLLAMA_URL = config.OLLAMA_URL
TEXT_MODEL = config.OLLAMA_TEXT_MODEL  # any solid local instruct model works
VISION_MODEL = config.OLLAMA_VISION_MODEL  # for scanned/image-only PDFs
MIN_CHARS_PER_PAGE = 40  # below this, treat the page as scanned

SYSTEM_PROMPT = (
    "You extract development-permit facts from municipal text. Return ONLY a JSON "
    "object matching the schema. Use null for anything not explicitly stated — never "
    "guess or infer. Copy numbers exactly as written. If the text describes multiple "
    "building uses, list each in occupancy_types. Set development_class to the dominant "
    'use, or "mixed" when residential and non-residential uses are combined. Provide a '
    "confidence from 0 to 1 reflecting how completely the text supported the fields."
)

# JSON schema handed to Ollama's `format` param — mirrors references/extraction_prompt.md
SCHEMA = {
    "type": "object",
    "properties": {
        "development_name": {"type": ["string", "null"]},
        "address": {"type": ["string", "null"]},
        "permit_type": {"type": ["string", "null"]},
        "development_class": {"type": ["string", "null"]},
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


def _chat(messages: list[dict], model: str) -> dict:
    """Call Ollama /api/chat with JSON-schema-constrained output."""
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": messages,
            "format": SCHEMA,  # constrains the model to valid JSON
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=300,
    )
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    return json.loads(content)


def extract_from_text(text: str, model: str = TEXT_MODEL) -> dict:
    """Extract permit fields from a block of prose or PDF-derived text."""
    text = (text or "").strip()
    if not text:
        return {"development_class": "unknown", "occupancy_types": [], "confidence": 0.0}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Extract the fields from this text:\n\n{text}"},
    ]
    return _chat(messages, model)


def _page_image_b64(page) -> str:
    pix = page.get_pixmap(dpi=200)
    return base64.b64encode(pix.tobytes("png")).decode()


def extract_from_pdf(
    path: str | Path, model: str = TEXT_MODEL, vision_model: str = VISION_MODEL
) -> dict:
    """Extract from a PDF. Uses text where present, vision OCR where the PDF is scanned."""
    path = Path(path)
    if pdfplumber is None:
        raise RuntimeError("pip install pdfplumber")

    text_parts: list[str] = []
    scanned_pages = 0
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            t = pg.extract_text() or ""
            if len(t.strip()) >= MIN_CHARS_PER_PAGE:
                text_parts.append(t)
            else:
                scanned_pages += 1

    full_text = "\n\n".join(text_parts).strip()

    # Enough real text → text model path.
    if len(full_text) >= MIN_CHARS_PER_PAGE:
        return extract_from_text(full_text, model=model)

    # Otherwise the PDF is image-only → vision path on rasterized pages.
    if fitz is None:
        raise RuntimeError("Scanned PDF needs OCR: pip install pymupdf")
    doc = fitz.open(path)
    images = [_page_image_b64(doc[i]) for i in range(min(len(doc), 8))]  # cap pages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Extract the development-permit fields from these page images.",
            "images": images,
        },
    ]
    return _chat(messages, vision_model)


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
