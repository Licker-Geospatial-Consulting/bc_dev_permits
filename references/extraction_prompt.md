# Field extraction prompt (PDF text only)

This contract is for the **Ollama PDF-fallback path** only. HTML pages and map attributes are 
parsed deterministically by `bc_dev_permits/features.py` and never reach a model. Use this when 
extracting fields from PDF-derived text (or scanned-page images). Feed the model the raw 
text and require JSON out. `bc_dev_permits/modeling/predict.py` sends this as the system prompt with 
a JSON-schema `format`, so the local model is constrained to valid output.

## System prompt

> You extract development-permit facts from municipal text. Return ONLY a JSON object
> matching the schema. Use `null` for anything not explicitly stated — never guess or
> infer. Copy numbers exactly as written. If the text describes multiple building uses
> (e.g. shops on the ground floor and apartments above), list each in
> `occupancy_types`. Set `development_class` to the dominant use, or `"mixed"` when
> residential and non-residential uses are combined. Provide a `confidence` from 0 to
> 1 reflecting how completely the text supported the fields.

## JSON schema (also passed as Ollama `format`)

```json
{
  "type": "object",
  "properties": {
    "development_name":       {"type": ["string","null"]},
    "address":                {"type": ["string","null"]},
    "permit_type":            {"type": ["string","null"]},
    "development_class":      {"type": ["string","null"],
                               "enum": ["residential","commercial","mixed","industrial","institutional","unknown",null]},
    "status":                 {"type": ["string","null"]},
    "floor_area":             {"type": ["number","null"]},
    "footprint_area":         {"type": ["number","null"]},
    "number_of_stories":      {"type": ["integer","null"]},
    "units_total":            {"type": ["integer","null"]},
    "unit_mix":               {"type": ["object","null"]},
    "occupancy_types":        {"type": "array", "items": {"type": "string"}},
    "rental_or_strata":       {"type": ["string","null"],
                               "enum": ["rental","strata","mixed","unknown",null]},
    "rental_subtype":         {"type": ["string","null"]},
    "parking_vehicle_stalls": {"type": ["integer","null"]},
    "parking_bike_stalls":    {"type": ["integer","null"]},
    "parking_notes":          {"type": ["string","null"]},
    "building_materials":     {"type": ["string","null"]},
    "retrofit_info":          {"type": ["string","null"]},
    "zoning_density":         {"type": ["string","null"]},
    "energy_info":            {"type": ["string","null"]},
    "mechanical_system_info": {"type": ["string","null"]},
    "confidence":             {"type": "number"}
  },
  "required": ["development_class","occupancy_types","confidence"]
}
```

## Worked example (real North Vancouver prose)
This uses the North Vancouver prose only to show the target JSON shape. 
In practice that particular text lives on an **HTML** page, so it's parsed 
by `bc_dev_permits/features.py`, not here. This contract kicks in when equivalent 
descriptive text appears **inside a PDF** (e.g. a staff report) on the fallback path.

Input text from 115 East 18th Street:

> 1480821 B.C LTD. and MA Architects have submitted an application to rezone the
> property to allow for the construction of a 6-storey, purpose built rental building.
> ... one level of underground parking ... providing for 21 vehicle parking stalls and
> 56 bike stalls. Of the proposed 40 residential units, 19 are one-bedroom, 12 are
> two-bedroom, 3 are three-bedroom and 6 are suites. 4 of the 40 units are proposed to
> be Mid-Market Rental units with 36 at market rental rates.

Expected output:

```json
{
  "development_name": "115 East 18th Street",
  "address": null,
  "permit_type": "Rezoning",
  "development_class": "residential",
  "status": null,
  "floor_area": null,
  "footprint_area": null,
  "number_of_stories": 6,
  "units_total": 40,
  "unit_mix": {"1-bed": 19, "2-bed": 12, "3-bed": 3, "suite": 6},
  "occupancy_types": ["residential"],
  "rental_or_strata": "rental",
  "rental_subtype": "purpose-built rental (4 mid-market, 36 market)",
  "parking_vehicle_stalls": 21,
  "parking_bike_stalls": 56,
  "parking_notes": "one level of underground parking",
  "building_materials": null,
  "retrofit_info": null,
  "zoning_density": null,
  "energy_info": null,
  "mechanical_system_info": null,
  "confidence": 0.83
}
```

Note how `address` and `status` are left `null` because they came from *elsewhere* on
the page (the breadcrumb/title and the milestones table) — the caller fills those from
the deterministic parse, not the LLM. The LLM only reasons over the prose it's given.
