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

# Spelled-out cardinals CNV uses in prose (1-20).
_WORD_TO_INT = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

# "<prefix>plex" building words -> the dwelling-unit count they imply.
_PLEX = {
    "duplex": 2,
    "triplex": 3,
    "fourplex": 4,
    "quadplex": 4,
    "quadruplex": 4,
    "fiveplex": 5,
    "sixplex": 6,
}

# A count token: comma-grouped digits, or a spelled cardinal. Longest words first so
# the alternation prefers "twenty" over "two" when both could start a match.
_NUM = r"(?:\d[\d,]*|" + "|".join(sorted(_WORD_TO_INT, key=len, reverse=True)) + r")"

# Optional parenthetical digit that often restates a spelled number: "eighteen (18)".
_PAREN = r"(?:\s*\(\s*(\d+)\s*\))?"


def _to_int(token: str | None) -> int | None:
    """Digit string or spelled cardinal ('eighteen') -> int, else None."""
    if token is None:
        return None
    token = token.strip().lower().replace(",", "")
    if token.isdigit():
        return int(token)
    return _WORD_TO_INT.get(token)


def _count(m: re.Match | None) -> int | None:
    """Resolve a count match to an int, preferring the parenthetical digit.

    Group 1 is the number token; group 2 (if present) is the parenthetical digit "(18)".
    """
    if m is None:
        return None
    if m.lastindex and m.lastindex >= 2 and m.group(2):
        return int(m.group(2))
    return _to_int(m.group(1))


def _search_count(text: str, *patterns: str) -> int | None:
    """First pattern that matches, resolved to an int (parenthetical digit preferred)."""
    for pat in patterns:
        v = _count(re.search(pat, text, re.IGNORECASE))
        if v is not None:
            return v
    return None


def _as_int(token: str) -> int:
    """Strict digit/cardinal -> int (kept for callers that expect a hard failure)."""
    v = _to_int(token)
    if v is None:
        raise KeyError(token)
    return v


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
    (
        r"residential|dwelling|apartment|townhouse|condominium|\bunits?\b"
        r"|duplex|triplex|fourplex|fiveplex|sixplex|quadruplex|quadplex|houseplex",
        "residential",
    ),
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


_STOREY = r"stor(?:e?y|ies|eys)"


def number_of_stories(text: str) -> int | None:
    """Return the storey count, tolerating "six (6) storeys" and ranges.

    A range ("two to six storey", "2 and 3 storeys", "2-6 storey") collapses to the
    tallest value, since number_of_stories is a single INTEGER in the schema.
    """
    m = re.search(
        rf"({_NUM})\s*(?:to|and|through|&|-|–|—)\s*({_NUM})[\s-]*{_STOREY}",  # noqa: RUF001
        text,
        re.IGNORECASE,
    )
    if m:
        vals = [v for v in (_to_int(m.group(1)), _to_int(m.group(2))) if v is not None]
        if vals:
            return max(vals)
    # Single value; skip a storey count that describes an *existing* building to keep, so
    # the proposal's height wins ("existing three story ... proposed six storeys" -> 6).
    matches = list(
        re.finditer(
            rf"({_NUM}){_PAREN}[\s-]*(?:plus\s+basement\s+)?{_STOREY}", text, re.IGNORECASE
        )
    )
    non_existing = [
        m for m in matches if not re.search(r"existing\W*$", text[: m.start()], re.IGNORECASE)
    ]
    chosen = non_existing or matches
    return _count(chosen[0]) if chosen else None


# Dwelling "component" building types whose counts add up to the project total
# ("5 townhouse units and 39 apartment units" is 44 dwellings).
_COMPONENTS = (
    ("townhouse", r"townhouse|townhome"),
    ("apartment", r"apartment"),
    ("detached", r"detached\s+(?:home|house)"),
)


def _component_counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for label, kw in _COMPONENTS:
        total, found = 0, False
        # "N [unit] <type>" first; only if that finds nothing, try "N <type> unit(s)".
        for pat in (
            rf"({_NUM}){_PAREN}[\s-]+(?:unit\s+)?(?:{kw})",
            rf"({_NUM}){_PAREN}[\s-]+(?:{kw})\s+units?",
        ):
            for m in re.finditer(pat, text, re.IGNORECASE):
                c = _count(m)
                if c:
                    total += c
                    found = True
            if found:
                break
        if found:
            out[label] = total
    return out


def _units_stated_total(text: str) -> int | None:
    """Return the largest explicitly stated "total of <N> units".

    When several are given ("167 market rental units ... for a total of 186 units"),
    the grand (largest) total is the answer.
    """
    totals = [
        c
        for m in re.finditer(
            rf"(?:for\s+a\s+)?total\s+of\s+({_NUM}){_PAREN}\s+(?:[\w-]+\s+){{0,2}}units?\b",
            text,
            re.IGNORECASE,
        )
        if (c := _count(m)) is not None
    ]
    return max(totals) if totals else None


def _units_headline(text: str) -> int | None:
    """Headline "<N> unit(s) ... building/development/housing"."""
    return _search_count(
        text,
        rf"({_NUM}){_PAREN}[\s-]+units?\s+(?:of\s+[\w-]+\s+)?(?:[\w-]+\s+){{0,2}}?"
        rf"(?:development|building|housing)\b",
    )


def _units_subdivision(text: str) -> int | None:
    """Return a subdivision's resulting lots / single-family homes.

    Handles "from one to two lots", "into two new single-family lots" and
    "two single-family units".
    """
    if not re.search(r"subdivi|\blots?\b", text, re.IGNORECASE):
        return None
    return _search_count(
        text,
        rf"(?:into|to)\s+({_NUM}){_PAREN}\s+(?:new\s+)?(?:single[\s-]family\s+)?lots?\b",
        rf"({_NUM}){_PAREN}\s+(?:new\s+)?single[\s-]family\s+(?:homes?|houses?|units?|lots?)\b",
    )


def _units_component_sum(text: str) -> int | None:
    """Sum of the distinct dwelling component types (townhouse + apartment + ...)."""
    comp = _component_counts(text)
    if not comp:
        return None
    return sum(comp.values()) if len(comp) > 1 else next(iter(comp.values()))


def _units_generic(text: str) -> int | None:
    """Match a generic "<N> residential/rental/strata/dwelling units" or bare "<N> unit(s)"."""
    return _search_count(
        text,
        rf"({_NUM}){_PAREN}\s+(?:[\w-]+\s+){{0,3}}?(?:residential|rental|strata|dwelling)"
        rf"(?:\s+[\w-]+){{0,1}}?\s+units?\b",
        rf"({_NUM}){_PAREN}[\s-]+units?\b",
    )


def _units_plex(text: str) -> int | None:
    """Map a "<prefix>plex" building word to its implied dwelling-unit count.

    "2 triplex buildings" is 2 x 3 = 6 dwellings, so a leading count multiplies the
    per-building figure; a bare "triplex" is just its own count.
    """
    for word, n in _PLEX.items():
        m = re.search(rf"({_NUM})\s+(?:new\s+)?{word}\s+buildings?\b", text, re.IGNORECASE)
        if m:
            c = _to_int(m.group(1))
            if c:
                return c * n
    for word, n in _PLEX.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            return n
    return None


# Unit-count strategies, most-specific first. units_total() returns the first that hits,
# so a new phrasing is added by writing one small strategy and slotting it in here.
_UNIT_STRATEGIES = (
    _units_stated_total,
    _units_headline,
    _units_subdivision,
    _units_component_sum,
    _units_generic,
    _units_plex,
)


def units_total(text: str) -> int | None:
    """Return the total dwelling/unit count across CNV's many phrasings.

    Tries each strategy in _UNIT_STRATEGIES in order and returns the first hit:
    an explicitly stated total, then a headline "<N> unit(s) ... building/development",
    then a subdivision's resulting lots, then the sum of distinct dwelling component
    types (townhouse + apartment), then a generic "<N> ... units", then a "<prefix>plex".
    Commercial/retail unit counts are handled by unit_type_mix() and never inflate the
    dwelling total.
    """
    for strategy in _UNIT_STRATEGIES:
        v = strategy(text)
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


# Building/unit TYPES (as opposed to bedroom types). "N <type>" or "N <type> unit(s)".
_UNIT_TYPES = (
    ("townhouse", r"townhouse|townhome"),
    ("apartment", r"apartment"),
    ("strata", r"strata"),
    ("principal", r"principal(?:\s+dwelling)?"),
    ("lock-off", r"lock[\s-]?off"),
    ("secondary-suite", r"accessory\s+dwelling|secondary\s+suite"),
    ("commercial", r"commercial"),
    ("detached", r"detached\s+(?:home|house)"),
)


def unit_type_mix(text: str) -> dict | None:
    """Count of building/unit TYPES, e.g. {"townhouse": 5, "apartment": 39}.

    Also handles {"principal": 6, "lock-off": 6}. This is the "count of unit types" the
    schema notes for unit_mix; complements unit_mix() which captures the bedroom split.
    """
    out: dict[str, int] = {}
    for label, kw in _UNIT_TYPES:
        m = re.search(
            rf"({_NUM}){_PAREN}[\s-]+(?:unit\s+)?(?:{kw})", text, re.IGNORECASE
        ) or re.search(
            rf"({_NUM}){_PAREN}[\s-]+(?:{kw})[\s-]+(?:unit|suite)s?", text, re.IGNORECASE
        )
        c = _count(m)
        if c:
            out[label] = c
    return out or None


def parking_vehicle(text: str) -> int | None:
    """Return the count of vehicle parking stalls/spaces, if stated.

    Handles "41 car parking spaces", "fourteen (14) vehicle parking stalls",
    "120 underground vehicle parking spaces", "five parking stalls", "5 off-street
    parking stalls", "additional eleven off-street parking stalls" and
    "parking for 11 vehicles". Bicycle counts are excluded (see parking_bike()).
    """
    fillers = r"(?:secure\s+|underground\s+|off[\s-]?street\s+|on[\s-]?site\s+|surface\s+|new\s+)*"
    return _search_count(
        text,
        rf"(?:additional\s+)?({_NUM}){_PAREN}\s+{fillers}(?:vehicle|car)\s+parking\s+(?:spaces?|stalls?)",
        rf"(?:additional\s+)?({_NUM}){_PAREN}\s+{fillers}parking\s+(?:spaces?|stalls?)",
        rf"parking\s+for\s+({_NUM}){_PAREN}\s+(?:vehicles?|cars?)",
    )


def parking_bike(text: str) -> int | None:
    """Return the count of bicycle parking stalls/spaces/storage, if stated.

    Handles "56 bike stalls", "11 bicycle parking spaces", "6 Bike storage" and
    "twenty-nine (29) secure bicycle parking stalls".
    """
    return _search_count(
        text,
        rf"({_NUM}){_PAREN}\s+(?:secure\s+)?(?:bike|bicycle)\s+(?:parking\s+)?(?:spaces?|stalls?|storage)",
    )


def parking_notes(text: str) -> str | None:
    """Return the first sentence describing parking, if present.

    Captures the underground-parking sentence used on larger applications, and also
    per-unit stall notes like "Each townhouse unit will have ... one on-site parking
    stall." — useful when parking is stated per unit rather than as a single total, so
    the descriptive detail is preserved even when parking_vehicle() finds no digit total.
    """
    m = re.search(r"([^.]*\b(?:underground parking|parking stall)[^.]*\.)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def zoning_density(text: str) -> str | None:
    """Return the density as "FSR <n>" when an FSR/FAR figure is stated."""
    m = re.search(r"(?:FSR|FAR|floor space ratio|density)[^\d]{0,12}([\d.]+)", text, re.IGNORECASE)
    return f"FSR {m.group(1)}" if m else None


def floor_area(text: str) -> tuple[float | None, str | None]:
    """Return (area, unit) for a single stated gross floor/site area — metric or acres.

    Per-use imperial areas ("22,200 sq ft of commercial space") are routed to
    floor_area_by_use() / dev_permit_occupancy instead, so they don't masquerade as a
    single top-level gross area here.
    """
    for pat, unit in (
        (r"([\d,]+(?:\.\d+)?)\s*(?:m2|m²|sq\.?\s?m|square\s?met\w*)", "m2"),
        (r"([\d,]+(?:\.\d+)?)\s*(?:acres?|\bac\b)", "acre"),
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "")), unit
    return None, None


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
        rf"((?:[a-z][a-z/&-]*\s+){{0,3}}?)"
        rf"(space|floor\s?area|gfa|units?|building|retail|commercial|residential|office)\b",
        text,
        re.IGNORECASE,
    ):
        phrase = (m.group(3) + m.group(4)).strip()
        use = _first_use(phrase)
        if use and use not in out:
            out[use] = {
                "floor_area": float(m.group(1).replace(",", "")),
                "floor_area_unit": _norm_area_unit(m.group(2)),
                "detail": phrase,
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
# used only to produce a rough confidence signal for HTML parses. Each entry is one
# "slot": a single field name, or a tuple of field names that forms an any-of slot
# (satisfied when ANY member is present). number_of_stories and units_total are grouped
# because a page that states either its height OR its unit count has given us a usable
# size signal, so either alone is enough to credit that slot. Each slot counts once
# toward the denominator, so grouping does not shrink the score of pages that state both.
_CONFIDENCE_KEYS = (
    "permit_type",
    "development_class",
    ("number_of_stories", "units_total"),
    "address",
    "floor_area",
    # "parking_vehicle_stalls", # removed as it was not AS IMPORTANT
    # "rental_or_strata", # removed as it was not AS IMPORTANT, can be imputed from development_class???
)


# Values that carry no real information, so they must not credit a confidence slot.
# "unknown" is the development_class sentinel returned when nothing classifies, so it is
# treated the same as a missing value here.
_EMPTY_VALUES = (None, "", [], {}, "unknown")


def has_value(value) -> bool:
    """Return True when a field holds real information (see _EMPTY_VALUES for what does not).

    Shared by harvesters so "is this field populated" is judged the same way everywhere the
    confidence scorer judges it (missing, blank, empty collection, or the 'unknown' sentinel).
    """
    return value not in _EMPTY_VALUES


# --------------------------------------------------------------------------- #
# Field provenance (extraction method per field)
#
# Every stored permit field records HOW it was extracted, so QA can separate values we can
# trust byte-for-byte from values a model guessed. DETERMINISTIC methods are repeatable rule
# based parses (page HTML, regex over PDF text, stat-card geometry); NON_DETERMINISTIC methods
# come from a local LLM and can vary run to run. needs_review keys off this, not off "is it a
# PDF": a letter whose fields were all regex-parsed is trusted, a plan set read by vision is not.
# --------------------------------------------------------------------------- #
METHOD_HTML = "html"  # deterministic: parsed from the page prose/detail
METHOD_PDF_REGEX = "pdf_text_regex"  # deterministic: features parse over the PDF's text
METHOD_PDF_GEOMETRY = "pdf_geometry"  # deterministic: stat-card label/value bbox pairing
METHOD_PDF_MODEL_TEXT = "pdf_model_text"  # non-deterministic: local text LLM
METHOD_PDF_MODEL_VISION = "pdf_model_vision"  # non-deterministic: local vision LLM
DETERMINISTIC_METHODS = frozenset({METHOD_HTML, METHOD_PDF_REGEX, METHOD_PDF_GEOMETRY})
NONDETERMINISTIC_METHODS = frozenset({METHOD_PDF_MODEL_TEXT, METHOD_PDF_MODEL_VISION})

# The permit-content fields we track provenance for (everything a reviewer reads). Excludes
# bookkeeping columns (permit_id, source_url, status, milestones, documents, ...).
CONTENT_FIELDS = (
    "permit_type",
    "development_class",
    "occupancy_types",
    "number_of_stories",
    "units_total",
    "unit_mix",
    "rental_or_strata",
    "rental_subtype",
    "parking_vehicle_stalls",
    "parking_bike_stalls",
    "parking_notes",
    "zoning_density",
    "floor_area",
    "floor_area_unit",
)


def _slot_present(fields: dict, slot: str | tuple[str, ...]) -> bool:
    """Return True if a confidence slot is satisfied.

    A slot is a single field name, or an any-of tuple of names that counts as satisfied
    when any one of them holds a usable value (see has_value for what does not count).
    """
    names = (slot,) if isinstance(slot, str) else slot
    return any(has_value(fields.get(name)) for name in names)


def score_confidence(fields: dict) -> float:
    """Rough 0-1 confidence: the fraction of expected slots (see _CONFIDENCE_KEYS) filled.

    Shared by every municipality harvester so scores are comparable across sources. Pass
    the FULL assembled row (prose-extracted fields plus harvester-provided ones such as
    address), not just the prose output, so slots like "address" are credited.
    """
    present = sum(1 for slot in _CONFIDENCE_KEYS if _slot_present(fields, slot))
    return round(present / len(_CONFIDENCE_KEYS), 2)


def extract_all(text: str) -> dict:
    """Run every deterministic extractor over prose text; return schema-shaped fields."""
    text = re.sub(r"\s+", " ", text or "").strip()
    occ = occupancy_types(text)
    tenure, subtype = rental_or_strata(text)
    # Structured occupancy rows: each use, with a per-use floor area attached when stated.
    areas = floor_area_by_use(text)
    occupancies = [{"occupancy": o, **areas.get(o, {})} for o in occ]
    fa, fa_unit = floor_area(text)
    # "mixed-use" is an explicit signal on its own: some descriptions name it without
    # separately keywording both a residential and a commercial use.
    dev_class = development_class(occ)
    if dev_class != "mixed" and re.search(r"mixed[\s-]?use", text, re.IGNORECASE):
        dev_class = "mixed"
    out = {
        "permit_type": permit_type(text),
        "development_class": dev_class,
        "occupancy_types": occ,
        "occupancies": occupancies,
        "number_of_stories": number_of_stories(text),
        "units_total": units_total(text),
        # Bedroom split when stated; otherwise the building/unit-type split.
        "unit_mix": unit_mix(text) or unit_type_mix(text),
        "rental_or_strata": tenure,
        "rental_subtype": subtype,
        "rental_mix": rental_mix(text),
        "parking_vehicle_stalls": parking_vehicle(text),
        "parking_bike_stalls": parking_bike(text),
        "parking_notes": parking_notes(text),
        "zoning_density": zoning_density(text),
        "floor_area": fa,
        "floor_area_unit": fa_unit,
    }
    # Provisional prose-only score; harvesters finalize it over the full row (with the
    # harvester-provided address) via score_confidence(row).
    out["extraction_confidence"] = score_confidence(out)
    return out
