"""Langley PDF worklist builder - makes the related-document traversal a repeatable pipeline step.

The deterministic parser (bc_dev_permits.features) reads only a folder's HTML prose, so a folder
whose detail page is thin is flagged `needs_pdf_extraction` and left at a low confidence. The
richer fields (unit mix, floor area, FSR, parking) live in the application PDFs - and often in a
RELATED active application (the rezoning or the sibling development permit), not the folder's own
documents. langley.select_pdf_sources already assembles those candidates in priority order (own
documents newest-first, then each ACTIVE related application, rezoning first); this tool drives it.

For each queued row it downloads and keyword-scans every candidate PDF, renders the highest-signal
project-data page to PNG, and writes a worklist JSON plus a ranked console summary. The rendered
pages feed the Claude-in-session read pass (tools/langley_enrich.py). This tool does NOT read,
classify, or fill fields - by design (see .claude/CLAUDE.md and the pdf-parsing memory, PDFs are
read by Claude in-session, not an automated LLM). It only surfaces WHERE the data lives so the read
pass is systematic instead of ad hoc.

Usage:
    python tools/langley_pdf_queue.py                  # all un-read flagged rows, one per project
    python tools/langley_pdf_queue.py --limit 40       # cap to the 40 highest-signal data pages
    python tools/langley_pdf_queue.py --permit DP101299 RO100224
    python tools/langley_pdf_queue.py --all            # include rows already Claude-read
    python tools/langley_pdf_queue.py --out tmp/queue --dpi 140
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import time

from bc_dev_permits import config
from bc_dev_permits.harvesters import langley

# Project-data table keywords. A page that names several of these is a project-statistics / unit
# summary / site-data sheet - the page the read pass wants, not a cover or an elevation.
_DATA_KW = re.compile(
    r"civic address|project (?:data|statistics|summary|information)|site stat|development (?:data|summary)"
    r"|unit (?:summary|mix|breakdown)|apartment unit|zoning (?:summary|analysis|compliance|reconciliation)"
    r"|floor area|gross floor|building area|units?\b|f\.?s\.?r|f\.?a\.?r|lot area|lot coverage|density"
    r"|site area|parking|building height|storey",
    re.IGNORECASE,
)

# Rendering/scan bounds. Keep them modest so a queue run stays polite to the host and quick.
MAX_CANDIDATE_DOCS = 6  # own + related docs scanned per row, in select_pdf_sources priority order
MAX_PAGES_PER_DOC = 45  # pages keyword-scanned per PDF
MIN_DATA_SCORE = 4  # a page scoring >= this is treated as a real data page (else a p1 fallback)
FETCH_DELAY_SEC = 0.4  # politeness pause between PDF downloads (per-host rate limiting)

# Type priority when picking ONE representative permit per physical project (by address): the DP and
# the rezoning hold the staff report and the full drawing set, the variance/subdivision rarely do.
_TYPE_RANK = {
    "DP": 5,
    "RO": 4,
    "RZ": 3,
    "AL": 2,
    "TT": 2,
    "SA": 1,
    "DV": 1,
    "TU": 1,
    "LP": 1,
    "SC": 1,
}


def _addr_key(address: str | None) -> str | None:
    """Order-independent signature of an address (its set of numbers) for grouping one project."""
    if not address:
        return None
    return " ".join(sorted(set(re.findall(r"\d+", address)))) or None


def _type_rank(permit_id: str) -> int:
    """Return the report-richness rank of a permit from its id prefix (DP/RO highest)."""
    m = re.match(r"[A-Za-z]+", permit_id)
    return _TYPE_RANK.get(m.group(0), 0) if m else 0


def _has_sources(row: dict) -> bool:
    """Return True when the row has its own documents or an ACTIVE related application to read."""
    if row.get("documents"):
        return True
    return any(r.get("status") == "ACTIVE" for r in (row.get("related_permits") or []))


def select_queue(
    rows: list[dict],
    *,
    permit_ids: list[str] | None = None,
    include_read: bool = False,
) -> list[str]:
    """Return the permit ids to render, newest/highest-value first. Pure (no I/O) so it is testable.

    Default queue: every row flagged needs_pdf_extraction (or carrying documents / an ACTIVE related
    application) that has not yet been Claude-read, collapsed to ONE representative per physical
    project (grouped by civic address, richest permit type first). `permit_ids` renders exactly those
    ids (no project grouping). `include_read` keeps rows already enriched.
    """
    by_id = {r["permit_id"]: r for r in rows}
    if permit_ids:
        return [p for p in permit_ids if p in by_id]

    def is_read(r: dict) -> bool:
        return "claude_read" in (r.get("extraction_method") or "")

    members = defaultdict(list)
    for r in rows:
        members[_addr_key(r.get("address"))].append(r["permit_id"])

    groups: dict = defaultdict(list)
    loose: list[str] = []
    for r in rows:
        pid = r["permit_id"]
        # Only rows with something to render: own documents or an ACTIVE related application. (The
        # harvester sets needs_pdf_extraction on exactly these, so a flag without sources is a no-op.)
        if not _has_sources(r):
            continue
        if not include_read and is_read(r):
            continue
        key = _addr_key(r.get("address"))
        if key is None:
            loose.append(pid)
            continue
        # Skip a project that already has a Claude-read sibling (its data is captured there).
        sibs = [p for p in members[key] if p != pid]
        if not include_read and any(is_read(by_id[p]) for p in sibs):
            continue
        groups[key].append(pid)

    reps = []
    for _key, pids in groups.items():
        reps.append(max(pids, key=lambda p: (_type_rank(p), len(by_id[p].get("documents") or []))))
    reps += loose
    reps.sort(key=lambda p: (-_type_rank(p), -len(by_id[p].get("documents") or []), p))
    return reps


def _collapse_spaced(line: str) -> str:
    """Rejoin letter-by-letter spaced text ('P R O J E C T' -> 'PROJECT') so keyword scans match.

    Some architectural title blocks render each glyph separated by a space; without this a real
    data sheet scores zero on the keyword scan and is mistaken for a cover page.
    """
    out, buf = [], []
    for tok in line.split(" "):
        if len(tok) == 1 and tok.isalnum():
            buf.append(tok)
            continue
        if len(buf) >= 2:
            out.append("".join(buf))
        elif buf:
            out.append(buf[0])
        buf = []
        out.append(tok)
    if len(buf) >= 2:
        out.append("".join(buf))
    elif buf:
        out.append(buf[0])
    return " ".join(out)


def _page_score(text: str) -> int:
    """Count the DISTINCT project-data keywords on a page (more distinct terms = a richer table)."""
    norm = " ".join(_collapse_spaced(line) for line in text.splitlines())
    return len({m.group(0).lower() for m in _DATA_KW.finditer(norm)})


def _best_page(session, pid: str, cands: list[dict], out_dir: Path, dpi: int) -> dict:
    """Render the highest-scoring project-data page across a row's candidate PDFs.

    Falls back to page 1 of the first readable document when nothing scores as a data sheet (so the
    read pass still gets a cover with the address and project name).
    """
    import pymupdf  # optional dep, see pyproject [pdf]

    best = {"score": -1, "png": None, "doc": None, "src": None, "page": None, "pdf": None}
    fallback = None
    for cand in cands[:MAX_CANDIDATE_DOCS]:
        doc = cand["document"]
        src = cand["source_url"].split("folderNumber=")[-1]
        try:
            data = session.get(doc["url"], timeout=config.HTTP_TIMEOUT).content
            pdf = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:  # a document that cannot be fetched or opened is skipped
            print(f"    skip {src}: {doc.get('title')!r} ({exc!r:.40})")
            continue
        for i in range(min(pdf.page_count, MAX_PAGES_PER_DOC)):
            sc = _page_score(pdf[i].get_text())
            if sc > best["score"]:
                best = {
                    "score": sc,
                    "doc": doc["title"],
                    "src": src,
                    "page": i + 1,
                    "pdf": pdf,
                    "png": f"{pid}_{src}_{i + 1}.png".replace("/", "_"),
                }
        if fallback is None:
            fn = f"{pid}_p1.png"
            pdf[0].get_pixmap(dpi=dpi).save(str(out_dir / fn))
            fallback = {"score": 0, "png": fn, "doc": doc["title"], "src": src, "page": 1}
        time.sleep(FETCH_DELAY_SEC)

    if best["pdf"] is not None and best["score"] >= MIN_DATA_SCORE:
        best["pdf"][best["page"] - 1].get_pixmap(dpi=dpi).save(str(out_dir / best["png"]))
        return {k: best[k] for k in ("score", "png", "doc", "src", "page")}
    return fallback or {"score": -1, "png": None, "doc": None, "src": None, "page": None}


def build_worklist(ids: list[str], out_dir: Path, dpi: int) -> list[dict]:
    """Render each queued row's best data page and return worklist entries (also written to disk)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {r["permit_id"]: r for r in _load_rows()}
    session = langley._build_session()
    work = []
    for n, pid in enumerate(ids, 1):
        row = rows.get(pid)
        if row is None:
            continue
        try:
            detail = langley._cached_get(session, row["source_url"])
            cands = langley.select_pdf_sources(
                detail, row["source_url"], lambda u: langley._cached_get(session, u)
            )
        except Exception as exc:
            print(f"[{n}/{len(ids)}] {pid}: select failed ({exc!r:.50})")
            continue
        page = _best_page(session, pid, cands, out_dir, dpi)
        entry = {
            "permit_id": pid,
            "address": row.get("address"),
            "permit_type": row.get("permit_type"),
            "candidate_docs": len(cands),
            "data_page_score": page["score"],
            "png": page["png"],
            "source_folder": page["src"],
            "source_doc": page["doc"],
            "source_page": page["page"],
        }
        work.append(entry)
        tag = "DATA" if (page["score"] or 0) >= MIN_DATA_SCORE else "thin"
        print(
            f"[{n}/{len(ids)}] {pid:10} {tag} score {page['score']!s:>3}  {len(cands)} docs  "
            f"{page['png']!s}"
        )
    work.sort(key=lambda e: -(e["data_page_score"] or -1))
    (out_dir / "worklist.json").write_text(
        json.dumps(work, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rich = sum(1 for e in work if (e["data_page_score"] or 0) >= MIN_DATA_SCORE)
    print(
        f"\nworklist: {len(work)} rows ({rich} with a rich data page) -> {out_dir / 'worklist.json'}"
    )
    return work


def _load_rows() -> list[dict]:
    path = Path(config.PROCESSED_DATA_DIR) / "langley.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    """Parse CLI arguments, select the queue, and render each row's best data page."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--permit", nargs="*", help="render these exact permit ids (skips project grouping)"
    )
    parser.add_argument("--all", action="store_true", help="include rows already Claude-read")
    parser.add_argument("--limit", type=int, default=None, help="cap the queue to the top N rows")
    parser.add_argument("--out", default=None, help="output directory for PNGs + worklist.json")
    parser.add_argument("--dpi", type=int, default=140, help="render resolution (default 140)")
    args = parser.parse_args()

    rows = _load_rows()
    ids = select_queue(rows, permit_ids=args.permit, include_read=args.all)
    if args.limit is not None:
        ids = ids[: args.limit]
    out_dir = (
        Path(args.out)
        if args.out
        else Path(config.PROCESSED_DATA_DIR).parent / "pdf_queue" / "langley"
    )
    print(f"queued {len(ids)} Langley rows -> rendering best data pages into {out_dir}\n")
    build_worklist(ids, out_dir, args.dpi)


if __name__ == "__main__":
    main()
