"""Download a Let's Talk Delta application PDF and surface the pages that carry the project data.

Helper for the Claude-read enrichment pass (NOT part of the harvest). Given a download_file URL it
caches the PDF, prints the text of pages that mention the program keywords (units / storeys /
parking / floor area / FSR / statistics), and renders the best candidate pages to PNG so a tabular
project-data / site-statistics sheet (an image on an architectural set) can be read visually.

    python tools/delta_pdf_probe.py <download_url> [--render P1,P2] [--dpi 170] [--all-text]
"""
import argparse
import hashlib
from pathlib import Path
import re
import sys

import fitz  # pymupdf
import requests

CACHE = Path("tools/pdf_queue/delta")
UA = "bc-dev-permit-scraper/0.1 (+contact: himalya@lgeo.co)"
_KEYWORDS = re.compile(
    r"\b(unit|storey|stories|storeys|parking|bicycle|floor area|floor space ratio|FSR|"
    r"statistics|project data|development data|site area|lot area|dwelling|apartment|townhouse|"
    r"height|gross floor)\b", re.IGNORECASE)


def fetch(url: str) -> Path:
    """Download the PDF to the cache dir (by URL hash) and return the local path."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = CACHE / f"{key}.pdf"
    if not path.exists():
        r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def main() -> None:
    """Probe one Delta application PDF: dump its stats pages' text, or render pages to PNG."""
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--render", help="comma-separated 1-based page numbers to render to PNG")
    ap.add_argument("--dpi", type=int, default=170)
    ap.add_argument("--all-text", action="store_true", help="dump every page's text, not just hits")
    args = ap.parse_args()

    path = fetch(args.url)
    doc = fitz.open(path)
    print(f"PDF: {path} | pages: {doc.page_count} | {path.stat().st_size // 1024} KB")

    if args.render:
        for p in [int(x) for x in args.render.split(",")]:
            page = doc[p - 1]
            png = CACHE / f"{path.stem}_p{p}.png"
            page.get_pixmap(dpi=args.dpi).save(png)
            print(f"rendered page {p} -> {png}")
        return

    # Rank pages by keyword density and print the densest ones' text (likely the stats sheet / prose).
    scored = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        hits = len(_KEYWORDS.findall(text))
        scored.append((hits, i, text))
    scored.sort(reverse=True)
    pages = range(doc.page_count) if args.all_text else [i for _, i, _ in scored[:4]]
    for i in sorted(pages):
        text = doc[i].get_text()
        hits = len(_KEYWORDS.findall(text))
        flat = re.sub(r"\n{2,}", "\n", text).strip()
        print(f"\n===== PAGE {i + 1} (keyword hits: {hits}, chars: {len(flat)}) =====")
        print(flat[:3500] if not args.all_text else flat)


if __name__ == "__main__":
    sys.exit(main())
