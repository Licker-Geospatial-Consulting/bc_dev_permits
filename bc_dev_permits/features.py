"""Deterministic field extraction from municipal prose.

Per the project rule: fields that appear in HTML / map content are parsed HERE with
plain regex — no LLM. Ollama is used ONLY for PDFs (see bc_dev_permits.modeling.predict),
on the fallback path when a page has no usable text.

Every extractor returns None when the value isn't stated, so callers can leave the
column NULL rather than guessing.
"""

from __future__ import annotations

import re

_WORD_TO_BED = {
    "studio": "studio",
    "one": "1-bed",
    "two": "2-bed",
    "three": "3-bed",
    "four": "4-bed",
    "five": "5-bed",
}

# Permit type keywords in priority order (a page may mention several; first wins as primary).
_PERMIT_TYPES = [
    (r"\brezon", "Rezoning"),
    (r"official community plan|\bocp\b amendment|\bocp amendment", "OCP Amendment"),
    (r"development variance permit|\bdvp\b", "Development Variance Permit"),
    (r"development permit|\bdp\b", "Development Permit"),
    (r"\bsubdivi", "Subdivision"),
    (r"temporary use permit|\btup\b", "Temporary Use Permit"),
]

# Occupancy / use keywords -> canonical occupancy label.
_OCCUPANCY = [
    (r"residential|dwelling|apartment|townhouse|condominium|\bunits?\b", "residential"),
    (r"retail|shops?|commercial", "commercial"),
    (r"office", "office"),
    (r"industrial|warehouse|light industrial", "industrial"),
    (r"institutional|school|church|civic|community centre|daycare|child care", "institutional"),
    (r"hotel|hospitality", "hotel"),
]

# Affordability qualifiers that pair with "rental" to give a market/mid-market split.
_RENTAL_QUAL = r"(market|mid-market|below-market|affordable|secured|purpose-built)"

# Area units seen in prose; normalized by _norm_area_unit() to 'sqft' or 'm2'.
_AREA_UNIT = r"(sq\.?\s?ft|sf|square\s?f(?:ee)?t|m2|m²|sq\.?\s?m|square\s?met\w*)"


def _first_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None


def _first_num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return float(m.group(1).replace(",", "")) if m else None


def number_of_stories(text: str) -> int | None:
    """Return the storey count, tolerating CNV's "six (6) storeys" form."""
    return _first_int(r"(\d+)\s*\)?\s*-?\s*stor(?:e?y|ies|eys)", text)


def units_total(text: str) -> int | None:
    """Return the total dwelling/unit count, if stated.

    Matches "40 residential units", "55 rental units", "proposed 40 units", or
    "a total of 55 rental units" (an adjective may sit between the count and "units").
    """
    for pat in (
        r"(\d+)\s+residential\s+units",
        r"(\d+)\s+dwelling\s+units",
        r"(\d+)\s+rental\s+units",
        r"proposed\s+(\d+)\s+units",
        r"total of\s+(\d+)\s+(?:[\w-]+\s+)?units",
    ):
        v = _first_int(pat, text)
        if v is not None:
            return v
    return None


def unit_mix(text: str) -> dict | None:
    """Return the bedroom mix as {"1-bed": 19, "suite": 6, ...}, or None."""
    mix: dict[str, int] = {}
    for n, word in re.findall(
        r"(\d+)\s+(?:are\s+|of\s+which\s+\d+\s+are\s+)?"
        r"(studio|one|two|three|four|five)[\s-]?bed(?:room)?s?",
        text,
        re.IGNORECASE,
    ):
        mix[_WORD_TO_BED[word.lower()]] = int(n)
    for n in re.findall(r"(\d+)\s+(?:are\s+)?suites?\b", text, re.IGNORECASE):
        mix["suite"] = int(n)
    for n in re.findall(r"(\d+)\s+studios?\b", text, re.IGNORECASE):
        mix["studio"] = int(n)
    return mix or None


def parking_vehicle(text: str) -> int | None:
    """Return the count of vehicle parking stalls/spaces, if stated."""
    for pat in (
        r"(\d+)\s+vehicle\s+parking\s+stalls",
        r"(\d+)\s+vehicle\s+(?:parking\s+)?stalls",
        r"(\d+)\s+parking\s+(?:spaces|stalls)",
        r"parking\s+for\s+(\d+)\s+vehicles?",
    ):
        v = _first_int(pat, text)
        if v is not None:
            return v
    return None


def parking_bike(text: str) -> int | None:
    """Return the count of bicycle parking stalls, if stated."""
    return _first_int(r"(\d+)\s+(?:bike|bicycle)\s+(?:parking\s+)?stalls", text)


def parking_notes(text: str) -> str | None:
    """Return the sentence mentioning underground parking, if present."""
    m = re.search(r"([^.]*\bunderground parking[^.]*\.)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def zoning_density(text: str) -> str | None:
    """Return the density as "FSR <n>" when an FSR/FAR figure is stated."""
    m = re.search(r"(?:FSR|FAR|floor space ratio|density)[^\d]{0,12}([\d.]+)", text, re.IGNORECASE)
    return f"FSR {m.group(1)}" if m else None


def floor_area(text: str) -> float | None:
    """Return the gross floor area in m² when stated in metric units."""
    return _first_num(r"([\d,]+(?:\.\d+)?)\s*(?:m2|m²|sq\.?\s?m|square met)", text)


def rental_or_strata(text: str) -> tuple[str | None, str | None]:
    """Return (tenure, subtype), e.g. ("rental", "mid-market rental")."""
    t = text.lower()
    subtype = None
    for phrase in (
        "purpose built rental",
        "purpose-built rental",
        "mid-market rental",
        "below-market rental",
        "market rental",
        "secured rental",
        "affordable rental",
        "strata",
        "condominium",
    ):
        if phrase in t:
            subtype = phrase.replace("purpose built", "purpose-built")
            break
    if "rental" in t and "strata" not in t:
        return "rental", subtype
    if any(w in t for w in ("strata", "condominium", "for sale")):
        return "strata", subtype
    return None, subtype


def rental_mix(text: str) -> dict | None:
    """Split of rental units by affordability, e.g. {'market-rental': 49, 'mid-market-rental': 6}.

    Handles CNV's "49 ... market-rental units and six (6) ... mid-market rental units" —
    the count may be a bare digit or a "(6)" beside a spelled-out number.
    """
    mix: dict[str, int] = {}
    for m in re.finditer(
        rf"(\d+)\s*\)?[^\d]{{0,40}}?{_RENTAL_QUAL}[\s-]?rental", text, re.IGNORECASE
    ):
        key = f"{m.group(2).lower().replace(' ', '-')}-rental"
        mix.setdefault(key, int(m.group(1)))  # first mention wins per tenure
    return mix or None


def _norm_area_unit(raw: str) -> str:
    return "sqft" if re.search(r"ft|sf|feet", raw, re.IGNORECASE) else "m2"


def _first_use(phrase: str) -> str | None:
    for pat, label in _OCCUPANCY:
        if re.search(pat, phrase, re.IGNORECASE):
            return label
    return None


def floor_area_by_use(text: str) -> dict:
    """Per-use floor areas from 'N <unit> of <use> space' -> {use: {floor_area, unit, detail}}.

    A figure shared by several uses (e.g. 'commercial and office space') is attached to the
    first canonical use in the phrase, with the full phrase preserved in `detail`. Used to
    populate dev_permit_occupancy.floor_area rather than the top-level gross floor area.
    """
    out: dict[str, dict] = {}
    for m in re.finditer(
        rf"([\d,]+(?:\.\d+)?)\s*{_AREA_UNIT}\.?\s+of\s+"
        rf"([a-z][a-z,\s/&-]+?)\s+(?:space|floor\s?area|gfa)\b",
        text,
        re.IGNORECASE,
    ):
        use = _first_use(m.group(3))
        if use and use not in out:
            out[use] = {
                "floor_area": float(m.group(1).replace(",", "")),
                "floor_area_unit": _norm_area_unit(m.group(2)),
                "detail": m.group(3).strip() + " space",
            }
    return out


def permit_type(text: str) -> str | None:
    """Return the primary permit type (first keyword match), if any."""
    for pat, label in _PERMIT_TYPES:
        if re.search(pat, text, re.IGNORECASE):
            return label
    return None


def occupancy_types(text: str) -> list[str]:
    """Return the canonical building uses mentioned, in priority order."""
    found: list[str] = []
    for pat, label in _OCCUPANCY:
        if re.search(pat, text, re.IGNORECASE) and label not in found:
            found.append(label)
    return found


def development_class(occ: list[str]) -> str:
    """Classify a permit from its occupancy list (residential/mixed/…)."""
    has_res = "residential" in occ
    non_res = [o for o in occ if o != "residential"]
    if has_res and non_res:
        return "mixed"
    if has_res:
        return "residential"
    if "industrial" in occ:
        return "industrial"
    if "institutional" in occ:
        return "institutional"
    if any(o in occ for o in ("commercial", "office", "hotel")):
        return "commercial"
    return "unknown"


# ---------------------------------------------------------------------------

# The set of fields we *hope* to find in a typical residential/mixed application,
# used only to produce a rough confidence signal for HTML parses.
_CONFIDENCE_KEYS = (
    "permit_type",
    "number_of_stories",
    "units_total",
    "parking_vehicle_stalls",
    "rental_or_strata",
)


def extract_all(text: str) -> dict:
    """Run every deterministic extractor over prose text; return schema-shaped fields."""
    text = re.sub(r"\s+", " ", text or "").strip()
    occ = occupancy_types(text)
    tenure, subtype = rental_or_strata(text)
    # Structured occupancy rows: each use, with a per-use floor area attached when stated.
    areas = floor_area_by_use(text)
    occupancies = [{"occupancy": o, **areas.get(o, {})} for o in occ]
    out = {
        "permit_type": permit_type(text),
        "development_class": development_class(occ),
        "occupancy_types": occ,
        "occupancies": occupancies,
        "number_of_stories": number_of_stories(text),
        "units_total": units_total(text),
        "unit_mix": unit_mix(text),
        "rental_or_strata": tenure,
        "rental_subtype": subtype,
        "rental_mix": rental_mix(text),
        "parking_vehicle_stalls": parking_vehicle(text),
        "parking_bike_stalls": parking_bike(text),
        "parking_notes": parking_notes(text),
        "zoning_density": zoning_density(text),
        "floor_area": floor_area(text),
    }
    present = sum(1 for k in _CONFIDENCE_KEYS if out.get(k) not in (None, [], {}))
    out["extraction_confidence"] = round(present / len(_CONFIDENCE_KEYS), 2)
    return out
