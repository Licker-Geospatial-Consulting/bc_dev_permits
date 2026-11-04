"""Assemble the standalone dashboard.html: inline Leaflet CSS + permit data + boundaries."""
import json
from pathlib import Path

D = Path("tools/dashboard")
tpl = (D / "dashboard.template.html").read_text(encoding="utf-8")
# Escape the template's few non-ASCII glyphs (all inside JS strings) so the page renders
# correctly regardless of how it's served; leave the injected data/CSS untouched. Only genuine
# non-ASCII characters are escaped here - never the ASCII hyphen, which appears in negative
# coordinates/offsets (e.g. setView([49.1,-123.1])) and must stay a hyphen or the JS breaks.
tpl = tpl.lstrip("﻿")  # drop a stray byte-order mark if present
for ch, esc in {"·": "\\u00b7", "©": "\\u00a9", "²": "\\u00b2", "↗": "\\u2197"}.items():
    tpl = tpl.replace(ch, esc)
css = (D / "leaflet.css").read_text(encoding="utf-8")
fonts = (D / "fonts.css").read_text(encoding="utf-8") if (D / "fonts.css").exists() else ""
tpl = tpl.replace("/*__FONTS__*/", fonts)
# Inline the lgeo brand mark as a data URI so the page stays self-contained.
mpath = D / "lgeo-mark.svg"
if mpath.exists():
    import base64
    mark = "data:image/svg+xml;base64," + base64.b64encode(mpath.read_bytes()).decode()
    tpl = tpl.replace("/*__MARK_URI__*/", mark)
permits = json.loads((D / "permits.json").read_text(encoding="utf-8"))
bpath = D / "boundaries.json"
boundaries = json.loads(bpath.read_text(encoding="utf-8")) if bpath.exists() else {"type": "FeatureCollection", "features": []}
rpath = D / "roads.json"
roads = json.loads(rpath.read_text(encoding="utf-8")) if rpath.exists() else {"type": "FeatureCollection", "features": []}

data_js = (
    "const PERMITS=" + json.dumps(permits, ensure_ascii=False, separators=(",", ":")) + ";\n"
    "const BOUNDARIES=" + json.dumps(boundaries, ensure_ascii=False, separators=(",", ":")) + ";\n"
    "const ROADS=" + json.dumps(roads, separators=(",", ":")) + ";"
)
html = tpl.replace("/*__LEAFLET_CSS__*/", css).replace("/*__DATA__*/", data_js)
out = D / "dashboard.html"
out.write_text(html, encoding="utf-8")
print("wrote", out, round(out.stat().st_size / 1024, 1), "KB",
      "| permits", len(permits), "| boundary features", len(boundaries.get("features", [])))
