"""City of Surrey harvester - PDF-first (council planning reports), Claude-read for the detail.

Surrey has no HTML detail pages: each application is a council **planning report PDF** listed on the
planning-reports page (a paginated Drupal view). So the attributes come from the PDF, not a web page.

Flow:  planning-reports list (permit id, address, status, dates, report PDF)  ->  dev_permit rows.

Every report PDF opens with a structured header - "Application No.", "PROPOSAL:" (a prose summary
plus bullet-point application types), "LOCATION:". That PROPOSAL summary carries the core program
("a 24-storey residential building with 230 rental units", "a 3,922 square metre industrial
building") and is parsed DETERMINISTICALLY with bc_dev_permits.features from the PDF's own text (no
model). The deeper pages (development data sheet: parking, unit mix, FSR) are read by **Claude
in-session, not Ollama** and applied with `apply_claude_pdf` (shared), which keeps the deterministic
values, stamps the PDF fields `pdf_claude`, and logs any disagreement to `row['conflicts']`.

Public API:
    get_report_rows(list_html, base)        -> list[dict]  (permit_id, address, status, dates, pdf)
    pdf_proposal(pdf_bytes)                 -> dict         (description, permit_types, zoning, text)
    parse_report(meta, proposal)            -> dict         (a dev_permit row)
    select_pdf_sources(row)                 -> list[dict]   (the report PDF, for the Claude read)
    apply_claude_pdf(row, fields, document) -> int
    harvest(limit=None, use_cache=True)     -> list[dict]
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import pdf_enrich

LIST_URL = config.SURREY_LIST_URL
BASE = "https://www.surrey.ca"
MUNICIPALITY = "surrey"
MIN_PROSE_CHARS = 20
MAX_PAGES = 30  # safety cap on list pagination
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Surrey application number, e.g. "7926-0234-00"; it is the PDF file name's stem (PLR_<id>.pdf).
_APP_NO = re.compile(r"\b\d{4}-\d{4}-\d{2}\b")
# Canonical application types Surrey lists as bullets under PROPOSAL.
_PERMIT_TYPES = (
    ("Rezoning", r"rezoning|land use contract"),
    ("OCP Amendment", r"official community plan|ocp amendment"),
    ("Development Permit", r"development permit"),
    ("Development Variance Permit", r"development variance permit"),
    ("Temporary Use Permit", r"temporary use permit"),
    ("Subdivision", r"subdivision"),
    ("Housing Agreement", r"housing agreement"),
)
apply_claude_pdf = pdf_enrich.apply_claude_pdf


# --------------------------------------------------------------------------- #
# 1. List scraping (permit id, address, status, dates, report PDF)
# --------------------------------------------------------------------------- #
def get_report_rows(list_html: str, base: str = LIST_URL) -> list[dict]:
    """Return one entry per planning report on a list page: id, address, status, dates, pdf url."""
    soup = BeautifulSoup(list_html, "lxml")
    out = []
    for row in soup.select("div.views-row"):
        link = row.select_one("a[href$='.pdf'], a[href*='.pdf?']")
        if not link:
            continue
        pdf_url = urljoin(base, link["href"])
        body = row.select_one(".listing-view__item-body") or row
        h4 = body.find(["h4", "h3", "h2"])
        app = _APP_NO.search(h4.get_text(" ", strip=True) if h4 else "") or _APP_NO.search(pdf_url)
        if not app:
            continue
        # The address and the application date are the bare text nodes between the <h4> and the
        # status <span>; the <time> is the council meeting date.
        texts = [t.strip() for t in (h4.find_next_siblings(string=True) if h4 else [])
                 if t.strip()] if h4 else []
        address = texts[0] if texts else None
        status = (body.select_one("span.tag") or body.select_one(".tag"))
        meeting = body.find("time")
        out.append({
            "permit_id": app.group(0),
            "address": _clean(address),
            "status": status.get_text(" ", strip=True) if status else None,
            "meeting_date": meeting.get("datetime", "")[:10] if meeting else None,
            "report_date": _clean(texts[1]) if len(texts) > 1 else None,
            "pdf_url": pdf_url,
        })
    return out


# --------------------------------------------------------------------------- #
# 2. PDF page-1 PROPOSAL (parsed deterministically from the report's own text)
# --------------------------------------------------------------------------- #
def pdf_proposal(pdf_bytes: bytes) -> dict:
    """Return {description, permit_types, zoning, text} from a report PDF's page-1 header."""
    import pymupdf  # optional dep, see pyproject [pdf]

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page1 = doc[0].get_text() if doc.page_count else ""
    m = re.search(r"PROPOSAL:\s*(.+?)\s*(?:LOCATION:|OWNER:|APPLICANT:|ZONING:)",
                  page1, re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    # Application types are the bullet labels; the report format varies (prose then bullet types, or
    # a bullet type then its prose), so the types are detected anywhere in the block and the whole
    # block - bullet markers removed - is the description that features.py then parses.
    permit_types = [label for label, kw in _PERMIT_TYPES if re.search(kw, raw, re.IGNORECASE)]
    # Detect a rezoning's "from <current> to <proposed>" per bullet, so the proposed zone stops
    # at the bullet boundary rather than running into the next application type.
    zoning = None
    for bullet in re.split(r"[•·▪]", raw):
        zm = re.search(r"from\s+(.+?)\s+to\s+([A-Z0-9][A-Z0-9()\-/ ]*?)\s*(?:$|[,.])",
                       bullet.strip(), re.IGNORECASE)
        if zm:
            zoning = f"{zm.group(1).strip()} -> {zm.group(2).strip()}"
            break
    description = re.sub(r"[•·▪]", " ", raw).strip()
    description = re.sub(r"\s{2,}", " ", description)
    return {"description": description, "permit_types": permit_types, "zoning": zoning,
            "text": page1}


# Development classes whose reports carry a parking table worth reading.
_PARKING_CLASSES = {"residential", "mixed", "commercial", "industrial", "institutional"}


def _xc(word) -> float:
    return (word[0] + word[2]) / 2


def _proposed_value(words, anchor, xprop: float, band: float = 1e9) -> int | None:
    """Read the number on ``anchor``'s row that sits under the Proposed column.

    ``band`` caps how far the chosen cell may sit from the Proposed header x: a small band keeps a
    blank Proposed cell from picking up the Required column's number on the same row.
    """
    cells = [(_xc(w), int(w[4].replace(",", "").replace("*", "")))
             for w in words if abs(w[1] - anchor[1]) < 5 and re.fullmatch(r"\d[\d,]*\*?", w[4])]
    if not cells:
        return None
    x, v = min(cells, key=lambda c: abs(c[0] - xprop))
    return v if abs(x - xprop) <= band else None


def _component_sum(words, ny: float, ty: float, xprop: float) -> int | None:
    """Sum the Proposed component rows between the header and an untotalled "Total:" row.

    Some reports leave the "Total:" row blank and list the parts (commercial / residential /
    visitor) above it, so the vehicle total is their sum. Rows below "Total:" (tandem, accessible)
    are subsets and excluded. The column band keeps a part's Required number out of the sum.
    """
    row_ys: list[float] = []
    for y in sorted(w[1] for w in words if ny + 2 < w[1] < ty - 2):
        if not row_ys or y - row_ys[-1] > 5:
            row_ys.append(y)
    parts = [_proposed_value(words, (0, y, 0, y, ""), xprop, band=90) for y in row_ys]
    parts = [p for p in parts if p is not None]
    return sum(parts) if parts else None


def _proposed_columns(words, y0: float, y1: float) -> list[float]:
    """Return the x-centres of the distinct "Proposed" column headers between y0 and y1.

    A per-lot parking table repeats "Proposed" once per lot, so more than one centre means the
    single-lot figures are ambiguous and the caller should decline the read.
    """
    xs = sorted(_xc(p) for p in words if p[4].lower() == "proposed" and y0 < p[1] < y1)
    cols: list[float] = []
    for x in xs:
        if not cols or x - cols[-1] > 40:
            cols.append(x)
    return cols


def _parking_from_words(words) -> tuple[int | None, int | None]:
    """Read (vehicle, secure bicycle) from a page's words, scoped to the "Number of Stalls" table.

    A staff-report page often holds several Required/Proposed tables (density, setbacks, parking), so
    the read is anchored to the "Number of Stalls" row: the Proposed header just above it fixes the
    column, the single "Total" row between there and the "Bicycle" sub-header is the vehicle total,
    and the "Secure" row below is the long-term bicycle count. Ambiguity returns None, not a guess.
    """
    nos = next((w for w in words if w[4].lower() == "stalls"
                and any(o[4].lower() == "number" and abs(o[1] - w[1]) < 5 and o[0] < w[0]
                        for o in words)), None)
    if nos is None:
        return None, None
    ny = nos[1]
    # The Required/Proposed header sits just above "Number of Stalls"; a per-lot table repeats the
    # Proposed column, so require exactly one and that it be paired with a Required column.
    cols = _proposed_columns(words, ny - 45, ny)
    paired = [x for x in cols if any(r[4].lower() == "required" and _xc(r) < x for r in words
                                     if ny - 45 < r[1] < ny)]
    if len(paired) != 1:
        return None, None
    xprop = paired[0]
    bike_y = min((w[1] for w in words if w[4].lower() == "bicycle" and w[1] > ny), default=1e9)
    totals = [w for w in words if w[4].lower().rstrip(":") == "total" and ny < w[1] < bike_y]
    vehicle = None
    if len(totals) == 1:
        # The tabulated Total, or - when that row is left blank - the sum of the parts above it.
        # The band keeps a blank/offset Proposed cell from reading across to the Required column.
        vehicle = _proposed_value(words, totals[0], xprop, band=90) \
            or _component_sum(words, ny, totals[0][1], xprop)
    secure = next((w for w in words if w[4].lower() == "secure" and w[1] > ny), None)
    return vehicle, (_proposed_value(words, secure, xprop, band=90) if secure else None)


def _part5_header_y(words) -> float | None:
    """Return the y of a "Parking (Part 5)" header row, or None."""
    for w in words:
        if w[4].lower() != "parking":
            continue
        row = [o for o in words if abs(o[1] - w[1]) < 5 and o[0] >= w[0]]
        joined = " ".join(o[4].lower() for o in row)
        if "part" in joined and "5)" in joined.replace(" ", ""):
            return w[1]
    return None


def _parking_part5(words) -> tuple[int | None, int | None]:
    """Read (vehicle, secure bicycle) from a "Parking (Part 5)" table that has no Number-of-Stalls row.

    This is the other Surrey layout: the parking rows hang directly off a "Parking (Part 5)" header
    inside the zoning-comparison table, with the Proposed figure in the right-hand column. The single
    "Total" row is the vehicle count and a "Secure"/"Bicycle" row the long-term bicycle count. As with
    the Number-of-Stalls read, anything ambiguous returns None rather than a guess.
    """
    ny = _part5_header_y(words)
    if ny is None:
        return None, None
    # The Proposed column header sits on, or just below, the Parking (Part 5) row (not the zoning
    # table's "Proposed CD Zone" far above it). A per-lot table has two Proposed columns, so its
    # single-lot Total is ambiguous: require exactly one Proposed column, else decline.
    cols = _proposed_columns(words, ny - 6, ny + 30)
    if len(cols) != 1:
        return None, None
    xprop = cols[0]
    bike_y = min((w[1] for w in words if w[4].lower() in ("bicycle", "bike") and w[1] > ny), default=1e9)
    totals = [w for w in words if w[4].lower().rstrip(":") == "total" and ny < w[1] < bike_y]
    vehicle = _proposed_value(words, totals[0], xprop, band=90) if len(totals) == 1 else None
    # Bicycle: a "Secure" row (two-column variant) or a single "Bicycle:" row carrying the count.
    bike_row = next((w for w in words if w[4].lower() == "secure" and w[1] > ny), None) \
        or next((w for w in words if w[4].lower().rstrip(":") == "bicycle" and w[1] >= bike_y), None)
    bike = _proposed_value(words, bike_row, xprop, band=90) if bike_row else None
    return vehicle, bike


_APP_HEADER = re.compile(r"application no\.?:?\s*(\d{4}-\d{4}-\d{2})")


def _bundles_other_application(doc, npages: int, permit_id: str) -> bool:
    """Report whether the PDF carries another application's report (a multi-report agenda package).

    A bundled package has an "Application No." header for a different id on some page, so a parking
    table found on a page that is not this application's must not be read off it.
    """
    pid = permit_id.lower()
    for i in range(npages):
        if any(m.group(1) != pid for m in _APP_HEADER.finditer(doc[i].get_text().lower())):
            return True
    return False


def pdf_parking(pdf_bytes: bytes, permit_id: str | None = None) -> tuple[int | None, int | None]:
    """Return (vehicle total, secure bicycle) from a report's parking table.

    A high-PRECISION positional read of the Proposed column: the "Number of Stalls" table first
    (see _parking_from_words), then the "Parking (Part 5)" zoning-table layout (_parking_part5). A
    value is set only when the table is unambiguous, otherwise None is returned rather than a guess.

    ``permit_id`` guards against a bundled agenda package: when the PDF carries more than one report,
    a parking page is read only if its own "Application No." header carries this id, so one report's
    table is never read off another's page.
    """
    import pymupdf  # optional dep, see pyproject [pdf]

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    npages = min(doc.page_count, 60)  # a long agenda package can push the body table past page 40
    bundled = None  # computed lazily, only when a candidate page lacks this application's id
    for i in range(npages):
        page = doc[i]
        text = page.get_text().lower()
        if "number of stalls" not in text and "parking (part 5)" not in text:
            continue
        if permit_id and permit_id.lower() not in text:
            if bundled is None:
                bundled = _bundles_other_application(doc, npages, permit_id)
            if bundled:
                continue  # another application's page inside a bundled package
        words = page.get_text("words")
        vehicle, bike = _parking_from_words(words)
        if vehicle is None and bike is None:
            vehicle, bike = _parking_part5(words)
        if vehicle is not None or bike is not None:
            return vehicle, bike
    return None, None


# --------------------------------------------------------------------------- #
# 2b. Development data sheet (the "Proposal" summary table, parsed deterministically)
# --------------------------------------------------------------------------- #
# A Surrey report's richest data is a single-column "Proposal"/"Proposed" summary table (FAR,
# height, floor area by use, unit mix, units) that can sit on any page - often deep in a report
# whose PROPOSAL header did not parse. It is read deterministically from the text layer, label line
# then value line, and a figure is emitted only when the table is unambiguous.
_AREA_RE = r"(?:sq\.?\s*m\.?|sqm|m²|m\s?2\b|square\s*met(?:re|er)s?)"
_UNIT_MIX_LABELS = {
    "studio": "studio", "bachelor": "bachelor", "micro unit": "micro", "micro": "micro",
    "1-bedroom": "1-bed", "2-bedroom": "2-bed", "3-bedroom": "3-bed", "4-bedroom": "4-bed",
    "1 bedroom": "1-bed", "2 bedroom": "2-bed", "3 bedroom": "3-bed", "4 bedroom": "4-bed",
}
_FLOOR_USE_LABELS = {
    "residential": "residential", "commercial": "commercial", "retail": "retail",
    "office": "office", "industrial": "industrial", "institutional": "institutional",
    "child care": "child_care", "childcare": "child_care", "hotel": "hotel",
}


def _table_num(s: str | None) -> float | None:
    # Tolerate a space after a thousands comma ("12, 061 square metres" is 12061, not 12).
    m = re.search(r"\d[\d,\s]*\.?\d*", s or "")
    return float(re.sub(r"[,\s]", "", m.group(0))) if m else None


def _table_int(s: str | None) -> int | None:
    n = _table_num(s)
    return int(n) if n is not None else None


def _area_value(line: str) -> float | None:
    """The single floor-area figure stated just before an area unit, or None if none/ambiguous.

    Anchoring to the unit avoids grabbing an unrelated number ("Site 1: 9,744 ..." is 9,744, not 1),
    and a value stating two areas (a multi-site cell) is ambiguous, so it is declined.
    """
    nums = re.findall(r"(\d[\d,\s]*\.?\d*)\s*" + _AREA_RE, line, re.IGNORECASE)
    return float(re.sub(r"[,\s]", "", nums[0])) if len(nums) == 1 else None


def proposal_table(text: str) -> dict:
    """Parse one page of a 'Proposal'/'Proposed' summary table (see the section note)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not re.search(r"\b(proposal|proposed)\b", " ".join(ln.lower() for ln in lines)):
        return {}
    out: dict = {}
    _scalar_rows(lines, out)
    _floor_area_section(lines, out)
    _unit_section(lines, out)
    # Only a page carrying a summary-specific row (floor-area breakdown or unit rows) is the summary
    # table; a bare FAR/height page is the RM-zone comparison table and must not qualify.
    if not any(k in out for k in ("floor_area_by_use", "units_total", "unit_mix")):
        return {}
    return out


def _scalar_rows(lines: list[str], out: dict) -> None:
    """Read the single-value rows (FAR, height/storeys, lot coverage) into out."""
    def nxt(pat):
        return next((lines[i + 1] for i in range(len(lines) - 1)
                     if re.fullmatch(pat, lines[i], re.IGNORECASE)), None)

    # A prefix like "Townhouse " is tolerated on the FAR and height labels (ground-oriented reports).
    far = nxt(r"(?:\w+\s+)?density\s*\(gross\):?|(?:\w+\s+)?floor area ratio(?:\s*\(far\))?:?"
              r"|density\s*\(far\):?|density:?|far:?")
    # A FAR is a small ratio, so a comma is a decimal separator ("0,46") not a thousands grouping.
    m = far and (re.search(r"(\d+[.,]?\d*)\s*(?:far|fsr)", far, re.IGNORECASE)
                 or re.fullmatch(r"(\d+[.,]?\d*)", far.strip()))
    if m:
        value = float(m.group(1).replace(",", "."))
        if 0 < value < 25:  # guard against a mis-read; Surrey FARs top out well below this
            out["far"] = value
    ht = nxt(r"(?:\w+\s+)?principal building:?|(?:\w+\s+)?building height:?|max(?:imum)? building height:?") or ""
    ms = re.search(r"(\d+)\s*stor(?:e|ie)", ht, re.IGNORECASE)
    if ms:
        out["storeys"] = int(ms.group(1))
    mm = re.search(r"(\d+\.?\d*)\s*m(?:etre|eter)?s?\b", ht, re.IGNORECASE)
    if mm:
        out["height_m"] = float(mm.group(1))
    cov = re.search(r"(\d+\.?\d*)\s*%", nxt(r"lot coverage:?") or "")
    if cov:
        out["lot_coverage"] = float(cov.group(1))


def _floor_area_section(lines: list[str], out: dict) -> None:
    """Read the 'Floor Area' section's per-use areas into out['floor_area_by_use']."""
    for i, ln in enumerate(lines):
        # A table header cell ("Floor Area", "Townhouse Floor Area"), not a prose line that happens
        # to end in "floor area".
        if len(ln) > 28 or not ln.lower().rstrip(":").endswith("floor area"):
            continue
        uses: dict = {}
        for j in range(i + 1, min(i + 12, len(lines) - 1)):
            lab = lines[j].lower().rstrip(":")
            if lab in ("residential units", "amenity space", "parking", "number of stalls"):
                break
            if lab in _FLOOR_USE_LABELS:
                v = _area_value(lines[j + 1])
                if v and v > 0:  # skip a "0" / "N/A 0 m2" use, and ambiguous multi-site values
                    uses[_FLOOR_USE_LABELS[lab]] = v
        if uses:
            out["floor_area_by_use"] = uses
        return


def _unit_section(lines: list[str], out: dict) -> None:
    """Read the 'Residential Units' section into out['units_total'] / out['unit_mix'].

    The text layer sometimes emits several labels then their values, so labels and values are
    collected in order and paired by position. The mix is trusted only when it sums to the Total.
    """
    start = next((i for i, ln in enumerate(lines)
                  if re.fullmatch(r"(?:\w+\s+)*residential units:?|unit (?:mix|breakdown):?", ln, re.IGNORECASE)),
                 None)
    if start is None:
        return
    labels, values = _unit_rows(lines[start + 1:start + 24])
    if len(labels) != len(values) or None in values:
        return
    pairs = list(zip(labels, values, strict=True))
    if "total" in labels:
        mapping = dict(pairs)
        total = mapping.pop("total")
        mix = {_UNIT_MIX_LABELS[k]: v for k, v in mapping.items() if k in _UNIT_MIX_LABELS}
    elif len(pairs) == 1 and pairs[0][0] in _UNIT_MIX_LABELS:
        # A single unit-type row with no Total line (e.g. "3-Bedroom: 30"): that count is the total.
        mix = {_UNIT_MIX_LABELS[pairs[0][0]]: pairs[0][1]}
        total = pairs[0][1]
    else:
        return
    out["units_total"] = total
    if mix and sum(mix.values()) == total:
        out["unit_mix"] = mix


def _unit_rows(lines: list[str]) -> tuple[list[str], list[int | None]]:
    """Collect the unit-section labels and values in order, up to and including the Total row."""
    labels, values = [], []
    for row in lines:
        lab = row.lower().rstrip(":")
        if lab == "total" or lab in _UNIT_MIX_LABELS:
            labels.append(lab)
        elif re.match(r"\d", row):
            values.append(_table_int(row))
        elif labels and values:
            break
        if "total" in labels and len(values) >= len(labels):
            break
    return labels, values


def _data_table_richness(d: dict) -> int:
    return sum(bool(d.get(k)) for k in ("floor_area_by_use", "unit_mix", "units_total", "far"))


def pdf_data_fields(pdf_bytes: bytes, permit_id: str | None = None) -> dict:
    """Return the development data sheet from a report PDF's "Proposal" summary table.

    The richest CONTIGUOUS run of summary-table pages is parsed as one table (a table can span two
    pages); distant tables are kept apart so their figures never leak in. ``permit_id`` applies the
    same bundled-agenda-package guard as pdf_parking.
    """
    import pymupdf  # optional dep, see pyproject [pdf]

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    npages = min(doc.page_count, 60)
    texts = [doc[i].get_text() for i in range(npages)]
    if permit_id and _bundles_other_application(doc, npages, permit_id):
        texts = [t if permit_id.lower() in t.lower() else "" for t in texts]
    groups = _contiguous_groups(proposal_table(t) for t in texts)
    if not groups:
        return {}
    best = max(groups, key=lambda g: sum(_data_table_richness(r) for r in g))
    return _merge_group(best)


def _contiguous_groups(parses) -> list[list[dict]]:
    """Group a page-ordered stream of parses into runs of consecutive non-empty results."""
    groups, run = [], []
    for res in parses:
        if res:
            run.append(res)
        elif run:
            groups.append(run)
            run = []
    if run:
        groups.append(run)
    return groups


def _merge_group(group: list[dict]) -> dict:
    """Merge the pages of one table: first non-empty value per field, union of floor-area uses."""
    merged: dict = {}
    for res in group:
        for k, v in res.items():
            if k == "floor_area_by_use":
                merged.setdefault(k, {})
                for uk, uv in v.items():
                    merged[k].setdefault(uk, uv)
            elif k not in merged:
                merged[k] = v
    return merged


def parse_report(meta: dict, proposal: dict) -> dict:
    """Build a dev_permit row from a report's list metadata + its parsed PROPOSAL."""
    desc = proposal.get("description") or ""
    prose = features.extract_all(desc if len(desc) >= MIN_PROSE_CHARS else "")
    row = {
        "municipality": MUNICIPALITY,
        "permit_id": meta["permit_id"],
        "source_url": meta["pdf_url"],
        "development_name": None,
        "address": meta.get("address"),
        "permit_type": ", ".join(proposal.get("permit_types") or []) or None,
        "status": meta.get("status"),
        "raw_text": desc or None,
        "extraction_method": "pdf_text",
        "milestones": _milestones(meta),
        "documents": [{"url": meta["pdf_url"], "title": f"Planning Report {meta['permit_id']}",
                       "doc_role": "planning_report"}],
        "is_parsed": bool(desc),
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }
    if desc:
        row.update({k: v for k, v in prose.items() if k != "permit_type"})  # keep bullet permit_type
        if proposal.get("zoning") and not features.has_value(row.get("zoning_density")):
            row["zoning_density"] = proposal["zoning"]
        row["is_parsed"] = True
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True
    # The report's data-sheet pages (parking, unit mix, FSR) are a Claude-read candidate whenever the
    # summary is low-signal or missing those richer fields.
    row["needs_pdf_extraction"] = pdf_enrich.needs_pdf(row) or any(
        not features.has_value(row.get(f)) for f in ("parking_vehicle_stalls", "unit_mix"))
    return row


def _apply_pdf_parking(row: dict, pdf_bytes: bytes) -> None:
    """Fill the parking stalls from a development report's "Number of Stalls" table, in place.

    Only development rows (those with a unit count or a residential/mixed class) are read, and the
    read is high-precision: a value is set only when the table is unambiguous, otherwise the row
    stays a parking Claude-read / review candidate.
    """
    if not (features.has_value(row.get("units_total"))
            or row.get("development_class") in _PARKING_CLASSES):
        return
    try:
        vehicle, bike = pdf_parking(pdf_bytes, row.get("permit_id"))
    except (OSError, ValueError, RuntimeError):
        vehicle = bike = None
    if vehicle is not None:
        row["parking_vehicle_stalls"] = vehicle
    if bike is not None:
        row["parking_bike_stalls"] = bike
    row["needs_pdf_extraction"] = pdf_enrich.needs_pdf(row) or not features.has_value(
        row.get("parking_vehicle_stalls"))


def _apply_pdf_data_table(row: dict, pdf_bytes: bytes) -> None:
    """Fill FAR / height / floor area / unit mix / units from the report's summary table, in place.

    The data sheet is authoritative for these fields, so a table value overrides a weaker PROPOSAL
    prose value and the disagreement is logged to row['conflicts']. A report whose PROPOSAL did not
    parse but whose data sheet did becomes a parsed row.
    """
    try:
        data = pdf_data_fields(pdf_bytes, row.get("permit_id"))
    except (OSError, ValueError, RuntimeError):
        return
    if not data:
        return
    for key, src in (("number_of_stories", "storeys"), ("units_total", "units_total"),
                     ("height_m", "height_m")):
        v = data.get(src)
        if v is None:
            continue
        old = row.get(key)
        if features.has_value(old) and old != v:
            row.setdefault("conflicts", []).append(
                {"field": key, "prose": {"value": old}, "table": {"value": v}, "kept": "table"})
        row[key] = v
    if data.get("unit_mix"):
        row["unit_mix"] = data["unit_mix"]
    if data.get("far") is not None:
        row["floor_area_ratio"] = data["far"]
    if data.get("lot_coverage") is not None:
        row["lot_coverage"] = data["lot_coverage"]
    _apply_floor_area_uses(row, data)
    row["is_parsed"] = True
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6
    row["needs_pdf_extraction"] = pdf_enrich.needs_pdf(row)


def _apply_floor_area_uses(row: dict, data: dict) -> None:
    """Set floor area, occupancies and the development class from the table's per-use areas."""
    fab = data.get("floor_area_by_use")
    if fab:
        row["floor_area"] = round(sum(fab.values()), 1)
        row["floor_area_unit"] = "m2"
        row["occupancies"] = [{"occupancy": u, "floor_area": a, "floor_area_unit": "m2"}
                              for u, a in fab.items()]
        row["occupancy_types"] = sorted(set((row.get("occupancy_types") or []) + list(fab)))
    elif data.get("units_total") and row.get("development_class") in (None, "unknown"):
        row["occupancy_types"] = sorted(set((row.get("occupancy_types") or []) + ["residential"]))
    else:
        return
    row["development_class"] = features.development_class(row["occupancy_types"])


def select_pdf_sources(row: dict) -> list[dict]:
    """Return the report PDF as the one document to Claude-read for the deeper data sheet."""
    return list(row.get("documents") or [])


# --------------------------------------------------------------------------- #
# 3. Live harvest (list is plain HTTP; each report PDF's text is parsed deterministically)
# --------------------------------------------------------------------------- #
def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest the planning-reports list + each report PDF's PROPOSAL into dev_permit rows."""
    session = session or _session()
    metas: list[dict] = []
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        html = _cached_get(session, f"{LIST_URL}?page={page}", use_cache)
        rows = [r for r in get_report_rows(html) if r["permit_id"] not in seen]
        if not rows:
            break
        seen.update(r["permit_id"] for r in rows)
        metas.extend(rows)
        if limit and len(metas) >= limit:
            break
    if limit:
        metas = metas[:limit]

    out = []
    for meta in metas:
        pdf_bytes = None
        try:
            pdf_bytes = _cached_get_bytes(session, meta["pdf_url"], use_cache)
            proposal = pdf_proposal(pdf_bytes)
        except (OSError, ValueError, RuntimeError):
            proposal = {"description": "", "permit_types": [], "zoning": None, "text": ""}
        row = parse_report(meta, proposal)
        if pdf_bytes:
            _apply_pdf_data_table(row, pdf_bytes)  # the summary-table data sheet (may resurrect a memo)
            _apply_pdf_parking(row, pdf_bytes)
        out.append(row)
    consolidate.consolidate_by_address(out)
    return out


# --------------------------------------------------------------------------- #
# HTTP + disk cache
# --------------------------------------------------------------------------- #
def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _cache_path(url: str, ext: str) -> str:
    return os.path.join(CACHE_DIR, f"surrey_{hashlib.sha256(url.encode()).hexdigest()[:16]}.{ext}")


def _cached_get(session, url: str, use_cache: bool = True) -> str:
    path = _cache_path(url, "html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def _cached_get_bytes(session, url: str, use_cache: bool = True) -> bytes:
    path = _cache_path(url, "pdf")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, "rb") as fh:
            return fh.read()
    content = session.get(url, timeout=config.HTTP_TIMEOUT).content
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return content


def _clean(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _milestones(meta: dict) -> list[dict]:
    out = []
    if meta.get("report_date"):
        out.append({"milestone": "Planning Report", "milestone_date": _iso_date(meta["report_date"])})
    if meta.get("meeting_date"):
        out.append({"milestone": "Council Meeting", "milestone_date": meta["meeting_date"]})
    return out


def _iso_date(s: str):
    from datetime import datetime
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
