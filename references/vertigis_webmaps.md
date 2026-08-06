# Reference: VertiGIS Studio webmap (Maple Ridge)

Maple Ridge's app (`apps.vertigisstudio.com/web/?app=...`) is **VertiGIS Studio Web**,
which is built on the Esri ArcGIS JS API. Like the ArcGIS apps, its layers are backed
by ArcGIS **FeatureServer/MapServer** services, so the same "query the REST service
directly" approach in `arcgis_webmaps.md` applies. The wrapper differs; the data plumbing
doesn't.

## Discovering the service

DevTools → Network → filter `FeatureServer` / `MapServer` / `query` while clicking a
development polygon. VertiGIS may also load an **app config JSON** listing the map's
service URLs — grep the network log for `.json` responses that contain
`"url": ".../FeatureServer"`. Record the layer URL(s) and field names in `COUNCIL.md`.

## The Maple Ridge specifics you flagged

- **A polygon can carry multiple application IDs.** This is the related-records shape:
  the development-area polygon has a relationship to an applications table. After you
  have a polygon's objectId, pull all of them:
  ```
  <layer>/queryRelatedRecords?objectIds=<oid>&relationshipId=<rid>&outFields=*&f=json
  ```
  Emit **one `dev_permit` per application record** returned, all sharing the polygon's
  geometry (→ same lat/lon).
- **"Application details" section** = the attribute fields on those related records.
  Map each to the schema (permit_id, permit_type, status, description/purpose, dates).
  Parse any prose attribute deterministically with `bc_dev_permits/features.py`; take 
  structured attributes (status, dates, id) directly. No LLM on map attributes.

If the layer has **no** relationship and instead stacks multiple features on the same
geometry, fall back to the plain paged `?where=1=1` query and emit one row per feature —
exactly as in the ArcGIS reference.

## Everything else

Geometry → representative lat/lon, `extraction_method='arcgis'`, raw attributes into
`raw_text`, parse-or-PDF fallback, polite paging with checksum-based change detection —
all identical to `arcgis_webmaps.md`.
