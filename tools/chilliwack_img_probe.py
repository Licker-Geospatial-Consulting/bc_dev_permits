"""Download a Build Chilliwack gallery image so it can be read with Claude.

Helper for the Claude-read gallery enrichment (NOT part of the harvest). Build Chilliwack keeps some
projects' richer data - a "Unit Count Summary" / project-data block - only in a gallery site-plan
image, not in the page prose or Project Details. This caches the image locally so Claude can read it
in-session; the extracted fields then go into tools/chilliwack_enrich.py.

    python tools/chilliwack_img_probe.py <image_url> [<image_url> ...]
"""
import hashlib
from pathlib import Path
import sys

import requests

CACHE = Path("tools/pdf_queue/chilliwack")
UA = "bc-dev-permit-scraper/0.1 (+contact: himalya@lgeo.co)"


def fetch(url: str) -> Path:
    """Download the image to the cache dir (named by URL hash + extension) and return its path."""
    CACHE.mkdir(parents=True, exist_ok=True)
    ext = url.rsplit(".", 1)[-1].split("?", maxsplit=1)[0].lower()
    ext = ext if ext in ("png", "jpg", "jpeg", "webp", "gif") else "png"
    path = CACHE / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.{ext}"
    if not path.exists():
        r = requests.get(url, headers={"User-Agent": UA}, timeout=90)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def main() -> int:
    """Download each image URL passed on the command line and print where it landed."""
    for url in sys.argv[1:]:
        path = fetch(url)
        print(f"{path}  ({path.stat().st_size // 1024} KB)  <- {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
