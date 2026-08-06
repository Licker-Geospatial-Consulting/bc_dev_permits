# Reference: ArcGIS webmaps (Coquitlam, Port Moody, New Westminster)

These sites look like interactive maps with clickable polygons, but the map is just a
UI over one or more **ArcGIS REST services** (FeatureServer / MapServer). The popup
you see when you click a polygon is that feature's *attributes*. So you don't automate
clicking — you query the service directly and get every feature as JSON.

This turns a "webmap" into a plain HTTP + JSON job. No Playwright needed once you have
the service URL.

## Step 1 — Find the service URL (one-time per city)

Open the app, then browser DevTools → Network → filter `FeatureServer` (or
`MapServer`, `query`). Click a polygon. You'll see a request like:

```
https://<host>/arcgis/rest/services/<folder>/<name>/FeatureServer/<layerId>/query?...
```

Copy the base up to `/query`. Confirm it in a browser: appending `?f=pjson` to the
layer URL prints its fields and metadata. Record the URL + the attribute field names
in `COUNCIL.md`.

Hosts to expect:
- **Coquitlam** — ArcGIS Experience Builder app; service under a Coquitlam ArcGIS host
  or `services.arcgis.com/<org>/...`.
- **Port Moody** — ArcGIS Web AppViewer; service under `portmoody.maps.arcgis.com` /
  a Port Moody host.
- **New Westminster** — ArcGIS Experience; the "Projects List" widget is backed by a
  layer too — query it to get the project rows + their detail-page URLs, then hand those
  URLs to `html_detail_pages.md`.

## Step 2 — Pull every feature

Query the layer with paging (services cap page size, often 1000/2000):

```
<layer>/query
  ?where=1=1
  &outFields=*
  &returnGeometry=true
  &outSR=4326           # WGS84 so geometry is lat/lon straight away
  &f=json
  &resultOffset=0
  &resultRecordCount=1000
```

Increment `resultOffset` by the page size until fewer than a full page returns. Each
result is `{ "attributes": {...}, "geometry": {...} }`.

- **lat/lon**: for polygons, compute a representative point (centroid of the ring, or
  `geometry.rings` → average) and store as `latitude`/`longitude`.
- **permit_id / description**: read from `attributes`. Map the city's field names to the
  schema (record the mapping in COUNCIL.md).

## Step 3 — Handle "multiple applications per polygon"

Two common shapes; check which the layer uses:

1. **Several features stacked on the same geometry** — the paged query already returns
   them as separate rows. Emit one `dev_permit` per feature. (Coquitlam: a single
   polygon may return more than one feature — parse **all** of them, not just the first.)
2. **A related table** (one polygon → many application records via a relationship) —
   the layer exposes `relationships` in its `?f=pjson`. Fetch related rows with:
   ```
   <layer>/queryRelatedRecords?objectIds=<id>&relationshipId=<rid>&outFields=*&f=json
   ```
   Emit one `dev_permit` per related record. (Maple Ridge behaves this way — see
   `vertigis_webmaps.md`.)

## Step 4 — Parse the description/purpose into fields

The useful attribute is usually a prose blob:
- **Port Moody**: parse the **`purpose`** attribute — it's a sentence describing the
  application — through `harvesters/fields.py`.
- **Coquitlam**: parse the popup `description` attribute the same way, once per feature.

Set `extraction_method='arcgis'`, keep the raw attribute JSON in `raw_text`, and apply
the same parse-or-PDF rule: if an attribute carries a document URL instead of prose,
store it in `dev_document` and set `needs_pdf_extraction=true`. 
so the Ollama PDF pass handles it. Ollama is never run on map attributes directly.

## Politeness

Query endpoints are public but shared. Page politely, cache responses by a checksum of
the attributes for change detection, and don't hammer — one pass per refresh cadence.
