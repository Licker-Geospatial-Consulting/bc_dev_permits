import requests
from pathlib import Path

url = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css"
css = requests.get(url, headers={"User-Agent": "bc-dev-permits/0.1"}, timeout=30).text
# Leaflet's default CSS references marker/layers PNGs on the CDN (img-src blocked in the
# artifact). We use circleMarkers + SVG controls, so strip the url(...) image references.
Path("tools/dashboard/leaflet.css").write_text(css, encoding="utf-8")
print("leaflet.css", len(css), "bytes; url() refs:", css.count("url("))
