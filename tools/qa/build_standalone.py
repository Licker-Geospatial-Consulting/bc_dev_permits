#!/usr/bin/env python3
"""Bundle the QA viewer + a dataset into ONE self-contained HTML (no server, no build step).

The normal viewer (tools/qa/index.html) fetches viewer.jsx and a JSON at runtime, so it needs
a static server. This produces a single file that embeds both, so it opens by double-click
(file://) and can be emailed or dropped on any static host - handy for sharing a prototype
when GitHub Pages is not available (e.g. a private repo without Enterprise). React and Babel
still load from a CDN, so the viewer needs internet access at open time.

Run it as `python tools/qa/build_standalone.py <data.json> <out.html> [title]`, e.g.
`python tools/qa/build_standalone.py data/processed/victoria_remote_machine.json
tools/qa/victoria-qa-standalone.html "Victoria Development Permit QA"`.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

# str.format template: literal CSS/JS braces are doubled ({{ }}); only {title}/{data}/{viewer}
# are substituted. Values substituted in are NOT re-scanned for braces, so JS/JSON braces in
# the viewer and data are safe.
_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>&#127959;</text></svg>" />
<script crossorigin src="https://unpkg.com/react@18.3.1/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.8/babel.min.js"></script>
<style>body {{ margin: 0; background: #f9fafb; }}</style>
</head>
<body>
<div id="root"></div>
<script type="application/json" id="qa-data">{data}</script>
<script type="text/plain" id="viewer-src">{viewer}</script>
<script>
  window.QA_DATA = JSON.parse(document.getElementById("qa-data").textContent);
  window.QA_DATA_NAME = {title_js};
  var src = document.getElementById("viewer-src").textContent;
  var code = Babel.transform(src, {{ presets: [["react", {{ runtime: "classic" }}]], filename: "viewer.jsx" }}).code;
  var s = document.createElement("script");
  s.text = code;
  document.body.appendChild(s);
</script>
</body>
</html>
"""


def _no_script_close(text: str) -> str:
    """Neutralize any '</script' so embedded content cannot terminate its host <script> early."""
    return text.replace("</script", "<\\/script")


def build(data_path: str, out_path: str, title: str) -> None:
    """Write a self-contained QA dashboard HTML embedding viewer.jsx and the given dataset."""
    viewer = (HERE / "viewer.jsx").read_text(encoding="utf-8")
    data = Path(data_path).read_text(encoding="utf-8")
    html = _TEMPLATE.format(
        title=title,
        title_js=json.dumps(title),
        data=_no_script_close(data),
        viewer=_no_script_close(viewer),
    )
    out = Path(out_path)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html) // 1024} KB) from {data_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "Dev Permit QA")
