"""Apply the in-session Claude-read PDF fields to Township of Langley application rows.

Run after `python -m bc_dev_permits.dataset --municipality langley --out json --limit 0`. The
Purpose prose on each detail page is already parsed deterministically by the harvester; this adds
the richer fields (parking, unit mix, floor area, storeys, lot area, ...) read by Claude in-session
from each application's PDFs, in the order langley.select_pdf_sources returns them:

  1. the application's OWN documents (newest first), then
  2. the documents of each ACTIVE related application (rezoning first), newest first.

So the related rezoning/variance folder is only read when the application's own documents are
absent or not parsable. Read the candidates in order and stop at the first PDF that yields fields.

Applied with langley.apply_claude_pdf (the shared merge/conflict/provenance, as in Victoria):
deterministic HTML is never overwritten and a genuine HTML-vs-PDF disagreement is logged to
row['conflicts']. Render a tabular project-data/site-plan sheet with pymupdf and read the PNG.

Workflow to populate CLAUDE_READ (see tools/westvan_enrich.py, tools/ubc_enrich.py for examples):
  * For each low-signal / document-bearing row, call langley.select_pdf_sources on its detail HTML
    to get the ordered PDF candidates, render the data page, read it, and record the fields here
    keyed by permit_id (folder number), with the source document.
"""

import json
from pathlib import Path

from bc_dev_permits import config, consolidate
from bc_dev_permits.harvesters import langley

# Auto-generated from the in-session Claude reads of each permit's PDF (50-permit batch).
CLAUDE_READ = {
    "DP101536": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 125,
            "unit_mix": {"townhouse": 125},
            "number_of_stories": 3,
            "lot_area": 28335.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 275,
            "zoning_density": "SR-2 -> CD, 17.9 UPA",
        },
        {
            "title": "Drawings - Architectural 2026-06-16",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=61211C260617154225052596&folderId=61211C260617144418932268",
        },
    ),
    "DP101541": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 4,
            "unit_mix": {"3-bed": 4},
            "number_of_stories": 2,
            "floor_area": 737.2,
            "floor_area_unit": "m2",
            "lot_area": 1295.14,
            "lot_area_unit": "m2",
            "height_m": 6.74,
            "zoning_density": "SR-1",
        },
        {
            "title": "Drawings - Architectural 2026-07-07",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=68263C260708133512443183&folderId=68263C260708122721871888",
        },
    ),
    "DP101544": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 2,
            "unit_mix": {"duplex": 2},
            "number_of_stories": 3,
            "floor_area": 553.2,
            "floor_area_unit": "m2",
            "lot_area": 529.73,
            "lot_area_unit": "m2",
            "zoning_density": "R-CL(A)",
        },
        {
            "title": "Site Plan - 2026-08-26",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=85322C260826153330440338&folderId=85322C260826152727488983",
        },
    ),
    "DP101530": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 211,
            "unit_mix": {"seniors-rental": 211},
            "number_of_stories": 6,
            "floor_area_ratio": 3.25,
            "rental_or_strata": "rental",
            "parking_vehicle_stalls": 69,
            "zoning_density": "SR-2 -> CD-221, 3.25 FSR",
        },
        {
            "title": "Report to Council - July 13, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=866EA2E9-916E-485E-9075-F59C90961D19&folderId=47498C260508154601958591",
        },
    ),
    "DP101532": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 3,
            "unit_mix": {"principal": 2, "secondary-suite": 1},
            "number_of_stories": 2,
            "lot_area": 661.0,
            "lot_area_unit": "m2",
            "zoning_density": "R-1B",
        },
        {
            "title": "Drawings - Architectural 2026-06-05",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=67626C260703082219904551&folderId=56697C260611105437966978",
        },
    ),
    "DP101510": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 26,
            "unit_mix": {"townhouse": 26},
            "number_of_stories": 3,
            "floor_area": 3603.4,
            "floor_area_unit": "m2",
            "lot_area": 8090.0,
            "lot_area_unit": "m2",
            "lot_coverage": 42.3,
            "parking_vehicle_stalls": 62,
            "zoning_density": "SR-2 -> CD, 13.3 UPA",
        },
        {
            "title": "Site Plan 2028-08-27",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=66796C250828095949233655&folderId=66796C250828093955706314",
        },
    ),
    "DP101543": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 4,
            "unit_mix": {"principal": 2, "secondary-suite": 2},
            "number_of_stories": 2,
            "floor_area": 418.3,
            "floor_area_unit": "m2",
            "lot_area": 809.37,
            "lot_area_unit": "m2",
            "zoning_density": "R-1E",
        },
        {
            "title": "Elevations - Architectural - 2026-07-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=B37FBB64-A202-45D5-8C6A-4BEB1A2B88F3&folderId=71580C260716084845729507",
        },
    ),
    "DP101534": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 28,
            "unit_mix": {"4-plex": 12, "3-plex": 6, "duplex": 10},
            "number_of_stories": 3,
            "lot_area": 6287.4,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 56,
            "zoning_density": "R-CL(RH)/R-CL(SD)",
        },
        {
            "title": "Site Plan 2026-06-12",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=56697C260615114339977453&folderId=56697C260615113937471677",
        },
    ),
    "DP101529": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 4,
            "unit_mix": {"duplex": 4},
            "number_of_stories": 2,
            "floor_area": 1324.9,
            "floor_area_unit": "m2",
            "lot_area": 1926.7,
            "lot_area_unit": "m2",
            "zoning_density": "R-1E",
        },
        {
            "title": "Site Plan 2026-04-27",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=42468C260428100111089113&folderId=42468C260428100030592681",
        },
    ),
    "DP101505": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 116,
            "unit_mix": {"3-bed": 116},
            "number_of_stories": 3,
            "floor_area": 16447.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.84,
            "lot_area": 19485.84,
            "lot_area_unit": "m2",
            "lot_coverage": 34.72,
            "parking_vehicle_stalls": 256,
            "zoning_density": "24 UPA, FAR 0.84",
        },
        {
            "title": "Drawings - Architectural 2025-08-05",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=59440C250805153008653461&folderId=59440C250805152700039612",
        },
    ),
    "DP101504": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 164,
            "number_of_stories": 4,
            "floor_area": 14234.0,
            "floor_area_unit": "m2",
            "lot_area": 8430.0,
            "lot_area_unit": "m2",
            "lot_coverage": 44.14,
            "zoning_density": "79 UPA",
        },
        {
            "title": "Drawings - Architectural 2025-07-22",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=D917E2E5-93F1-40BB-BE0B-1A5F41A39528&folderId=55007C250722154552427605",
        },
    ),
    # DP101518 has no own documents; the data comes from its ACTIVE related rezoning RZ100782,
    # whose site plan is a 60-lot residential subdivision (~464.5 m2 each).
    "DP101518": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "lot_area": 27870.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> small-scale multi-unit housing (subdivision, 60 lots)",
        },
        {
            "title": "RZ100782 Digital Resubmission - November 6 2025 - Site Plan",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=F7C9788E-5803-4DDC-A6C0-41A3E8CF51EA&folderId=72781C241118085412722434",
        },
    ),
    "DP101519": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "SSMUH 3 (subdivision, 37 lots)",
        },
        {
            "title": "Report to Council - January 26, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=E6A2DDC4-DB3D-4D6A-B132-3D4BC5C8D85F&folderId=98032C251202125720615546",
        },
    ),
    "DP101511": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {
            "title": "Drawings - Architectural 2025-09-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=68454C250903155231995853&folderId=67643C250903143858079958",
        },
    ),
    "DP101512": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "number_of_stories": 3,
        },
        {
            "title": "Drawings - Architectural 2025-09-18",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=73503C250919123356124287&folderId=73503C250919122137864364",
        },
    ),
    "DP101533": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "number_of_stories": 3,
            "height_m": 9.96,
        },
        {
            "title": "Elevations 2026-06-12",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=56697C260615100724407512&folderId=56697C260615100223086294",
        },
    ),
    "DP101535": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "number_of_stories": 6,
            "height_m": 23.66,
        },
        {
            "title": "Elevations 2026-06-16",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=56697C260616160623891939&folderId=56697C260616160331156222",
        },
    ),
    "DP101546": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 4,
            "unit_mix": {"detached": 4},
            "lot_area": 1776.5,
            "lot_area_unit": "m2",
        },
        {
            "title": "Site Plan 2026-09-02",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=87929C260903090417846396&folderId=87929C260903084954532655",
        },
    ),
    "DP101517": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 1,
            "number_of_stories": 2,
            "lot_area": 1966.6,
            "lot_area_unit": "m2",
        },
        {
            "title": "Site Plan 2025-11-13",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=92510C251114152628963738&folderId=92510C251114145447005696",
        },
    ),
    "AL100463": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "lot_area": 34000.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-1/RU-1 -> R-1D single family",
        },
        {
            "title": "Report to Council - January 27, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=D9552E36-B97C-47D1-BD92-9E5110B0F967&folderId=73893C241118094252043327",
        },
    ),
    "CR000026": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "floor_area": 61.9,
            "floor_area_unit": "m2",
            "number_of_stories": 1,
            "zoning_density": "C2",
        },
        {
            "title": "Site Plan 2026-09-02",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=87429C260902140551152545&folderId=87429C260902140350671282",
        },
    ),
    "DP101513": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "floor_area": 462.8,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.43,
            "lot_area": 1222.09,
            "lot_area_unit": "m2",
            "lot_coverage": 43.0,
            "number_of_stories": 1,
            "height_m": 4.57,
            "footprint_area": 462.8,
            "parking_vehicle_stalls": 12,
            "zoning_density": "C-2, FAR 0.43",
        },
        {
            "title": "2026-04-29 Architectural",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=77ACC010-0FE4-42D0-ACD0-86BE16B9C3EA&folderId=75911C251003093124262995",
        },
    ),
    "DP101506": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "floor_area": 4885.6,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.48,
            "lot_area": 10215.9,
            "lot_area_unit": "m2",
            "number_of_stories": 5,
            "height_m": 17.2,
            "zoning_density": "C-3, FSR 0.48",
        },
        {
            "title": "Drawings - Architectural 2025-08-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=608B516D-AECF-4DD2-ABF1-62E372621244&folderId=63853C250819161803831358",
        },
    ),
    "DP101548": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "floor_area": 3414.0,
            "floor_area_unit": "m2",
            "lot_area": 4300.0,
            "lot_area_unit": "m2",
            "lot_coverage": 24.0,
            "number_of_stories": 4,
            "height_m": 18.0,
            "parking_vehicle_stalls": 116,
            "parking_bike_stalls": 8,
            "zoning_density": "CD-57 (4.1)",
        },
        {
            "title": "Elevations - 2026-09-11",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=88857C260911134046630805&folderId=88857C260911133722958279",
        },
    ),
    "DP101527": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "floor_area": 11391.0,
            "floor_area_unit": "m2",
            "lot_area": 152752.6,
            "lot_area_unit": "m2",
            "number_of_stories": 2,
            "zoning_density": "C-1",
        },
        {
            "title": "Drawings - Architectural 2026-06-02",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=2C139AA5-244B-4B45-80D3-196F54928CF9&folderId=36345C260407105513194610",
        },
    ),
    # Read across the full drawing set: the cover (DP.001) and site plan (DP.101) carry no civic
    # address, but the Site Stats & Survey sheet (DP.002) has the project-stats table with the
    # address, building height and parking. (General rule: gather fields across every PDF, and take
    # a missing address from the PDF's project-summary/development table or site plan.)
    "DP101531": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "8500 200th Street",
            "floor_area": 2100.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.30,
            "lot_area": 7028.0,
            "lot_area_unit": "m2",
            "lot_coverage": 30.0,
            "number_of_stories": 1,
            "height_m": 8.16,
            "parking_vehicle_stalls": 69,
            "zoning_density": "C-18 -> C-1, FAR 0.30",
        },
        {
            "title": "Elevations 2026-05-12 (DP.002 Site Stats & Survey)",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=47498C260514094509979241&folderId=47498C260514094114292674",
        },
    ),
    "DP101540": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "floor_area": 895.9,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.13,
            "lot_area": 6899.6,
            "lot_area_unit": "m2",
            "lot_coverage": 10.6,
            "footprint_area": 733.6,
            "number_of_stories": 2,
            "height_m": 9.4,
            "parking_vehicle_stalls": 12,
            "parking_bike_stalls": 6,
            "zoning_density": "M-2, FAR 0.13",
        },
        {
            "title": "Drawings - Architectural 2026-06-29",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=64783C260629115818268811&folderId=64783C260629114503864325",
        },
    ),
    "DP101523": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial", "commercial"],
            "floor_area": 3973.0,
            "floor_area_unit": "m2",
            "number_of_stories": 1,
            "parking_vehicle_stalls": 35,
            "zoning_density": "M-2A",
        },
        {
            "title": "Site Plan 2026-03-16",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=20581C260316113431080712&folderId=20581C260316094456483082",
        },
    ),
    "DP101521": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "floor_area": 1821.0,
            "floor_area_unit": "m2",
            "number_of_stories": 1,
            "height_m": 13.44,
            "zoning_density": "M-5/M-3/M-4 Heavy Industrial",
        },
        {
            "title": "Report to Council - June 15, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=B74ABD18-B53E-4EA4-BCF5-B7CA126A802B&folderId=12446C260130142558350775",
        },
    ),
    "DP101542": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "floor_area": 25573.4,
            "floor_area_unit": "m2",
            "lot_area": 93032.0,
            "lot_area_unit": "m2",
            "number_of_stories": 2,
            "height_m": 15.65,
            "parking_vehicle_stalls": 213,
            "parking_bike_stalls": 20,
            "zoning_density": "M-2 General Industrial",
        },
        {
            "title": "Site Plan - 2026-07-08",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=68385C260708150622287863&folderId=68385C260708150356862619",
        },
    ),
    # SITE DATA table on A-1.0 (the site plan page of the drawing set, not the cover) - the cover
    # had no stats, so this was read on a second pass across the full set.
    "DP101507": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26477 56 Avenue",
            "number_of_stories": 2,
            "floor_area": 9517.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.50,
            "footprint_area": 7905.0,
            "lot_area": 19164.0,
            "lot_area_unit": "m2",
            "lot_coverage": 41.2,
            "parking_vehicle_stalls": 100,
            "zoning_density": "M-2, FAR 0.50",
        },
        {
            "title": "Drawings - Architectural 2025-08-20 (A-1.0 Site Plan / Site Data)",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=64713C250821095018325455&folderId=64132C250820161204468703",
        },
    ),
    "AL100480": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "lot_area": 22600.0,
            "lot_area_unit": "m2",
            "zoning_density": "RU-3 (ALR non-farm equipment storage)",
        },
        {
            "title": "Site Plan - 2026-04-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=39607C260416164333962727&folderId=39607C260416162541855364",
        },
    ),
    "DP101516": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "floor_area": 133.36,
            "floor_area_unit": "m2",
            "lot_area": 564.34,
            "lot_area_unit": "m2",
            "lot_coverage": 38.71,
            "height_m": 10.23,
            "number_of_stories": 2,
            "parking_vehicle_stalls": 9,
            "zoning_density": "C-2",
        },
        {
            "title": "Drawings - Architectural 2025-11-12",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=91754C251113155341958105&folderId=91754C251113141134987074",
        },
    ),
    "DP101525": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "floor_area": 2220.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.24,
            "lot_area": 9267.3,
            "lot_area_unit": "m2",
            "lot_coverage": 12.0,
            "number_of_stories": 2,
            "height_m": 8.0,
            "parking_vehicle_stalls": 117,
            "zoning_density": "SR-2 -> P-1 Civic Institutional, FAR 0.24",
        },
        {
            "title": "Elevations - Building 2026-03-31",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=35148C260402113812854228&folderId=35148C260402113153694706",
        },
    ),
    "DP101514": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "floor_area": 380.8,
            "floor_area_unit": "m2",
            "lot_area": 563.0,
            "lot_area_unit": "m2",
            "number_of_stories": 2,
            "parking_vehicle_stalls": 9,
            "zoning_density": "C-2",
        },
        {
            "title": "Drawings - Architectural 2025-10-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=4126F8ED-5436-4109-8987-488F4DC3B570&folderId=81429C251017092243318728",
        },
    ),
    "DP101509": (
        {
            "development_class": "mixed",
            "occupancy_types": ["commercial", "institutional"],
            "address": "6810 Glover Road",
            "number_of_stories": 2,
            "height_m": 11.0,
            "lot_area": 3209.0,
            "lot_area_unit": "m2",
            "lot_coverage": 29.0,
            "footprint_area": 931.0,
            "zoning_density": "C-2B",
        },
        {
            "title": "Drawings - Architectural 2025-08-27",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=66211C250827152229743082&folderId=66211C250827145014642431",
        },
    ),
    "DP101515": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "units_total": 113,
            "unit_mix": {"studio": 9, "1-bed": 31, "2-bed": 61, "3-bed": 12},
            "number_of_stories": 4,
            "height_m": 13.78,
            "parking_vehicle_stalls": 306,
            "lot_area": 8003.0,
            "lot_area_unit": "m2",
            "zoning_density": "FSR 1.86/1.64",
        },
        {
            "title": "Drawings - Architectural 2025-11-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=88428C251105141340796017&folderId=88428C251105125015383936",
        },
    ),
    # --- batch 2: recent Development Permits (read in-session; multi-PDF + address-from-PDF) ---
    "DP101476": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "number_of_stories": 2,
            "floor_area": 45238.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.62,
            "lot_area": 73064.0,
            "lot_area_unit": "m2",
            "lot_coverage": 60.0,
            "height_m": 14.5,
            "parking_vehicle_stalls": 289,
            "parking_bike_stalls": 32,
            "zoning_density": "RU-1 -> M-2, FSR 0.62",
        },
        {
            "title": "Drawings - Architectural 2025-01-17",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=89761C250120111358668305&folderId=89761C250120105640596827",
        },
    ),
    "DP101474": (
        {"address": "7242 208 Street", "floor_area_ratio": 2.76},
        {
            "title": "Drawings - Architectural 2025-01-31",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=5CC1A02C-8941-4455-A1E1-82B3387267D3&folderId=87572C250117092233050389",
        },
    ),
    "DP101471": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7883 199 Street",
            "units_total": 11,
            "unit_mix": {"townhouse": 11},
            "number_of_stories": 3,
            "zoning_density": "CD-96",
        },
        {
            "title": "Development Permit No. 101471 - May 26, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=457EABA2-3D5E-48D8-8E28-39868D440A33&folderId=83148C241219155630850760",
        },
    ),
    "DP101455": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20287 36A Avenue",
            "units_total": 50,
            "unit_mix": {"apartment": 12, "townhouse": 38},
            "zoning_density": "CD",
        },
        {
            "title": "Drawings - Architectural 2024-08-13",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A6453B6E-E503-4448-AC2E-CD73BDA15E92&folderId=44201C240815103641411940",
        },
    ),
    "DP101454": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "8146 200th Street",
            "units_total": 245,
            "unit_mix": {"apartment": 245},
            "number_of_stories": 6,
            "lot_area": 11465.0,
            "lot_area_unit": "m2",
            "floor_area_ratio": 2.5,
            "zoning_density": "SR-2 -> CD, FSR 2.5",
        },
        {
            "title": "Drawings - Architectural 2024-08-02",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=42465C240809111918638446&folderId=42465C240809100754636996",
        },
    ),
    "DP101443": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "9494 200th Street",
            "floor_area": 4575.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.51,
            "lot_area": 9422.58,
            "lot_area_unit": "m2",
            "lot_coverage": 48.55,
            "number_of_stories": 2,
            "height_m": 11.12,
            "parking_vehicle_stalls": 44,
            "parking_bike_stalls": 8,
            "zoning_density": "M-1A, FSR 0.51",
        },
        {
            "title": "Drawings - Architectural 2024-05-07",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=7C2B52A4-D6BD-4AF9-BD30-B14B6B867ECE&folderId=11782C240507091221932375",
        },
    ),
    "DP101502": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "21528 Old Yale Road",
            "units_total": 2,
            "floor_area": 358.8,
            "floor_area_unit": "m2",
            "lot_area": 629.0,
            "lot_area_unit": "m2",
            "lot_coverage": 34.36,
            "number_of_stories": 2,
            "height_m": 8.28,
            "zoning_density": "SK-1",
        },
        {
            "title": "Heritage Alteration Permit No. 101502 - June 15, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=F2FBAA15-ED9A-4659-B3FC-851BA055366C&folderId=48851C250704135140691807",
        },
    ),
    "DP101494": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 2,
            "floor_area": 267.6,
            "floor_area_unit": "m2",
            "number_of_stories": 2,
            "height_m": 8.49,
            "lot_coverage": 55.0,
            "zoning_density": "R-CL(SD)",
        },
        {
            "title": "Architectural Drawings 2025-07-17",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=8A82D725-FAF9-4C8A-8A0F-49D77B1A55D6&folderId=28701C250508111402884782",
        },
    ),
    "DP101484": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {
            "title": "Heritage Alteration Permit No. 101484 - May 12, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=B40790DC-DBC6-49DF-8D76-AADE73EFDA1D&folderId=06738C250314143253901142",
        },
    ),
    "DP101462": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7320 & 7290 208th Street",
            "units_total": 22,
            "unit_mix": {"townhouse": 22},
            "number_of_stories": 3,
            "floor_area": 3064.9,
            "floor_area_unit": "m2",
            "lot_area": 4561.4,
            "lot_area_unit": "m2",
            "lot_coverage": 47.7,
            "parking_vehicle_stalls": 51,
            "zoning_density": "SR-2 -> CD, 16-22 UPA",
        },
        {
            "title": "Drawings - Architectural 2024-10-02",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=3BBC263E-5E10-462E-B20F-F43493285DA1&folderId=58123C241003084320412827",
        },
    ),
    "DP101503": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "address": "9167 Glover Road",
            "parking_vehicle_stalls": 27,
            "zoning_density": "Fort Langley Heritage Conservation Area",
        },
        {
            "title": "Report to Council - November 3, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=654C4880-6855-43F7-9BC2-80A2461C842B&folderId=51930C250714111913348358",
        },
    ),
    "DP101498": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "9181, 9205 & 9215 Glover Road",
            "floor_area": 1677.0,
            "floor_area_unit": "m2",
            "lot_area": 4457.0,
            "lot_area_unit": "m2",
            "lot_coverage": 37.6,
            "parking_vehicle_stalls": 41,
            "zoning_density": "C-2",
        },
        {
            "title": "Drawings - Architectural 2025-05-27",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=6AC244E1-DD21-4EE1-8112-12E022E6BCD2&folderId=34739C250527111614940632",
        },
    ),
    "DP101497": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "institutional"],
            "address": "27251 30 Avenue",
            "units_total": 5,
            "unit_mix": {"apartment": 5},
            "number_of_stories": 3,
            "lot_area": 737.6,
            "lot_area_unit": "m2",
            "zoning_density": "C-2, FSR 1.1",
        },
        {
            "title": "Report to Council - June 29, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=17E7B2FC-A19E-43A5-9239-1463CC809DBE&folderId=34556C250527111308075385",
        },
    ),
    "DP101470": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20390 72 Avenue",
            "lot_area": 7151.73,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> CD",
        },
        {
            "title": "Report to Council - February 3, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=FCF6ED08-ECB8-4FC1-9966-B6554B724EF8&folderId=82218C241216131208823236",
        },
    ),
    "DP101481": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20371 27 Avenue",
            "units_total": 4,
            "unit_mix": {"principal": 2, "secondary-suite": 2},
            "floor_area": 317.2,
            "floor_area_unit": "m2",
            "lot_area": 690.0,
            "lot_area_unit": "m2",
            "number_of_stories": 2,
            "height_m": 9.0,
            "zoning_density": "R-1D",
        },
        {
            "title": "2026 03 13 Architectural Drawings",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=150219DD-C3BA-4EEA-9788-2E0D8BC152A8&folderId=03904C250305100927896720",
        },
    ),
    "DP101480": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20275 & 20311 32nd Avenue",
            "units_total": 62,
            "unit_mix": {"townhouse": 62},
            "lot_area": 11932.0,
            "lot_area_unit": "m2",
            "lot_coverage": 34.93,
            "parking_vehicle_stalls": 137,
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD, 21.1 UPA",
        },
        {
            "title": "Drawings - Architectural 2025-02-27",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=02279C250228150242433836&folderId=02279C250228144950916742",
        },
    ),
    "DP101464": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "27214 Fraser Highway",
            "units_total": 128,
            "unit_mix": {"apartment": 128},
            "number_of_stories": 6,
            "floor_area_ratio": 1.0,
            "parking_vehicle_stalls": 254,
            "parking_bike_stalls": 128,
            "zoning_density": "CD-192, FSR 1.00",
        },
        {
            "title": "Development Permit No. 101464 - April 7, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=05EEC8F6-0536-4780-97A0-5113940940E1&folderId=67797C241029134251318273",
        },
    ),
    "DP101446": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7950 202A Street",
            "units_total": 517,
            "unit_mix": {"apartment": 517},
            "number_of_stories": 6,
            "floor_area": 39570.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 2.5,
            "lot_area": 18500.0,
            "lot_area_unit": "m2",
            "lot_coverage": 46.6,
            "zoning_density": "CD-194, FSR 2.5",
        },
        {
            "title": "Drawings - Architectural Full Set 2024-06-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=24806C240617100041093390&folderId=20167C240603143600761843",
        },
    ),
    # --- batch 2b: more recent DPs (data sheets + related-app rezoning reports) ---
    "DP101491": (
        {
            "zoning_density": "CD-213 (apartments/seniors/daycare/commercial, max 1.4 FSR, 4 storeys)",
            "number_of_stories": 4,
        },
        {"title": "RZ100783: Report to Council - May 26, 2025", "url": None},
    ),
    "DP101490": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "zoning_density": "CD-213 (apartments/seniors/daycare/commercial, max 1.4 FSR, 4 storeys)",
            "number_of_stories": 4,
        },
        {
            "title": "Report to Council - May 26, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=CB1E68C2-00B7-4CF0-9B59-61B2DB851C4E&folderId=18498C250411132208137133",
        },
    ),
    "DP101489": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "CD-208 (townhouses, 13-18 UPA)",
        },
        {"title": "RO100246: Report to Council - October 21, 2024", "url": None},
    ),
    "DP101460": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "CD-208 (townhouses, 13-18 UPA)",
        },
        {
            "title": "Report to Council - October 21, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=EED01F9E-C8F6-4881-B4F0-1BF056D6AC04&folderId=55918C240924092837206139",
        },
    ),
    "DP101472": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "CD (Medium Density Apartment 2.5 FSR / Townhouse 22 UPA, max 6 storeys)",
        },
        {"title": "RO100269: Report to Council - September 11, 2023", "url": None},
    ),
    "DP101456": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "number_of_stories": 3,
            "zoning_density": "CD-215 (townhouses, max 22 UPA, 3 storeys)",
        },
        {"title": "RO100300: Report to Council - November 17, 2025", "url": None},
    ),
    "DP101467": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "6920 204 Street",
            "units_total": 31,
            "unit_mix": {"4-bed": 18, "3-bed": 13},
            "number_of_stories": 3,
            "floor_area": 4052.62,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.67,
            "lot_area": 6047.0,
            "lot_area_unit": "m2",
            "lot_coverage": 36.4,
            "parking_vehicle_stalls": 68,
            "zoning_density": "SR-2 -> CD, 20.8 UPA",
        },
        {"title": "RO100303: Drawings - Architectural 2025-04-11", "url": None},
    ),
    "DP101458": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "26700 30a Avenue",
            "units_total": 6,
            "unit_mix": {"townhouse": 6},
            "number_of_stories": 3,
            "floor_area": 1409.5,
            "floor_area_unit": "m2",
            "lot_area": 2513.95,
            "lot_area_unit": "m2",
            "lot_coverage": 29.0,
            "height_m": 9.0,
            "zoning_density": "RM-1",
        },
        {
            "title": "Drawings - Architectural 2024-09-09",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=D864AA2A-8DD7-478A-837F-90F76E78DD80&folderId=51836C240910152609637333",
        },
    ),
    "DP101463": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20230 72B Avenue",
            "units_total": 118,
            "unit_mix": {"1-bed": 67, "2-bed": 37, "3-bed": 14},
            "number_of_stories": 6,
            "floor_area": 8673.67,
            "floor_area_unit": "m2",
            "floor_area_ratio": 2.38,
            "lot_area": 3640.0,
            "lot_area_unit": "m2",
            "lot_coverage": 46.0,
            "parking_vehicle_stalls": 177,
            "parking_bike_stalls": 118,
            "zoning_density": "CD-193, FSR 2.38",
        },
        {
            "title": "Drawings - Architectural 2024-10-28",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=F128201E-5844-4964-B842-1B199A7E2265&folderId=67797C241028153053034896",
        },
    ),
    "DP101452": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7510 & 7488 198B Street",
            "units_total": 131,
            "unit_mix": {"studio": 1, "1-bed": 83, "2-bed": 27, "3-bed": 16},
            "number_of_stories": 6,
            "floor_area": 10690.0,
            "floor_area_unit": "m2",
            "parking_vehicle_stalls": 140,
            "parking_bike_stalls": 110,
        },
        {"title": "RO100295: Revised Architectural Drawings January 6 2025", "url": None},
    ),
    "DP101445": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "6982 206 Street",
            "units_total": 83,
            "unit_mix": {"townhouse": 83},
            "number_of_stories": 3,
            "floor_area": 11126.0,
            "floor_area_unit": "m2",
            "lot_area": 20235.0,
            "lot_area_unit": "m2",
            "lot_coverage": 37.4,
            "parking_vehicle_stalls": 183,
            "zoning_density": "SR-2 -> CD, 16.6 UPA",
        },
        {
            "title": "Drawings - Architectural 2024-05-31",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A9E37DE9-735C-468E-AD2D-72B40E900767&folderId=19483C240531091727017447",
        },
    ),
    "DP101444": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7838 197 Street",
            "units_total": 37,
            "unit_mix": {"townhouse": 37},
            "zoning_density": "SR-2 -> CD-190, 19.4 UPA",
        },
        {"title": "RZ100619: Report to Council - April 15, 2024", "url": None},
    ),
    "DP101442": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "198 Street",
            "units_total": 62,
            "unit_mix": {"townhouse": 62},
            "zoning_density": "CD (Latimer)",
        },
        {
            "title": "Development Permit No. 101442 - April 25, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=8BD19D1E-B39E-4890-ABB8-7F1C1724CE83&folderId=07894C240424121355799744",
        },
    ),
    "DP101468": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20354 71A Avenue & 7091 204 Street",
            "units_total": 40,
            "unit_mix": {"townhouse": 40},
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-214, 21 UPA",
        },
        {"title": "RO100304: Report to Council - October 6, 2025", "url": None},
    ),
    # --- batch 2c: recent DP site-plan / cover reads (image-only fallbacks) ---
    "DP101501": (
        {"address": "8143 198A Street"},
        {
            "title": "Development Permit No. 101501 - November 17, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=3AED2092-FF31-433E-B630-B23B9A71AEB8&folderId=46156C250626144238835520",
        },
    ),
    "DP101500": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "5115 214 Street",
            "units_total": 2,
            "unit_mix": {"detached": 2},
            "lot_area": 4200.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> SR-3 (2 lots)",
        },
        {
            "title": "Report to Council - July 27, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A459EEC9-5BA6-4B3A-80D8-2FE23DE6F247&folderId=45011C250624145618146170",
        },
    ),
    "DP101482": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "7278 208 Street",
            "number_of_stories": 6,
        },
        {
            "title": "Drawings - Architectural 2025-03-05",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=03904C250306121457103004&folderId=03904C250306115131363016",
        },
    ),
    "DP101488": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "8840 210th Street",
            "units_total": 4,
            "unit_mix": {"detached": 4},
        },
        {
            "title": "Site Plan 2025-03-19",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=08931C250320113217823634&folderId=07805C250320101841621473",
        },
    ),
    "DP101486": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "3365 264 Street",
            "number_of_stories": 2,
        },
        {
            "title": "Drawings - Architectural 2025-03-14",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=07376C250317161310617886&folderId=07376C250317160430524941",
        },
    ),
    "DP101475": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26477 56th Avenue",
        },
        {
            "title": "Site Plan 2025-01-17",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=87572C250117153248021737&folderId=87572C250117144614440203",
        },
    ),
    "DP101449": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "8508 206 Street",
            "units_total": 14,
            "unit_mix": {"detached": 14},
            "lot_area": 8094.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> R-CL(A)/R-CL(B), 7 UPA",
        },
        {
            "title": "Site Plan 2024-06-13",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=23890C240613162700150244&folderId=23890C240613162435531049",
        },
    ),
    "DP101499": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20996 83A Avenue",
            "units_total": 4,
            "unit_mix": {"townhouse": 4},
            "lot_area": 850.1,
            "lot_area_unit": "m2",
            "lot_coverage": 51.1,
            "zoning_density": "RCL-MH",
        },
        {
            "title": "2026-03-17 Site Plan",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=ED9C187D-48EF-495D-91D7-3EE5ABDC0B11&folderId=41364C250613100334054907",
        },
    ),
    "DP101496": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20320 80th Avenue",
            "number_of_stories": 6,
        },
        {
            "title": "Drawings - Architectural 2025-05-20",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=33138C250523094819959633&folderId=33138C250523092930445556",
        },
    ),
    "DP101495": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "19759 28th Avenue",
            "lot_area": 8383.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 43,
            "zoning_density": "industrial (truck parking)",
        },
        {
            "title": "Site Plan 2025-05-16",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=33138C250522135715435256&folderId=33138C250522123716031295",
        },
    ),
    "DP101473": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "204 Street",
            "units_total": 23,
            "unit_mix": {"detached": 23},
            "lot_area": 18946.0,
            "lot_area_unit": "m2",
            "zoning_density": "SSMUH subdivision (23 lots)",
        },
        {
            "title": "Site Plan 2025-01-13",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=87572C250114140051118264&folderId=87572C250114134313677286",
        },
    ),
    "DP101492": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "9292 200th Street",
        },
        {
            "title": "Drawings - Architectural 2025-04-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=20049C250415111136387683&folderId=19448C250414153350316428",
        },
    ),
    "DP101485": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "3350 262 Street",
            "number_of_stories": 2,
        },
        {
            "title": "Drawings - Architectural 2025-03-14",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=07376C250317123412263228&folderId=07376C250317115403212022",
        },
    ),
    "DP101479": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "19700 Block of 84 Avenue",
            "units_total": 1,
            "floor_area": 110.0,
            "floor_area_unit": "m2",
        },
        {
            "title": "Report to Council - April 27, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=5360B2C1-3243-425F-BE1B-4C9424B79623&folderId=98398C250218144055283843",
        },
    ),
    "DP101450": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20340, 20360 & 20388 80 Avenue",
            "number_of_stories": 6,
        },
        {
            "title": "Drawings 2024-06-19",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A0216600-26C5-457C-978B-E57A40E3299C&folderId=27017C240624135536878225",
        },
    ),
    "DP101478": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26264 30a Avenue",
            "number_of_stories": 2,
        },
        {
            "title": "Drawings - Architectural 2025-02-06",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=70793D66-D850-4669-9C2A-A1BA40A6BACC&folderId=96161C250207152341529558",
        },
    ),
    "DP101469": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "3685 207 Street",
        },
        {
            "title": "Development Permit No. 101469 - September 15, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=E2398A66-F4B5-4DBE-ABDC-C44B45BDF7E9&folderId=79864C241211114646175273",
        },
    ),
    "DP101451": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20315 & 20323 78 Avenue, 20320 & 20340 80 Avenue, 7831/7843/7849 204 Street",
            "units_total": 157,
            "unit_mix": {"townhouse": 157},
            "number_of_stories": 3,
            "floor_area": 22716.73,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.8,
            "lot_area": 29394.99,
            "lot_area_unit": "m2",
            "lot_coverage": 38.0,
            "parking_vehicle_stalls": 346,
            "parking_bike_stalls": 12,
            "zoning_density": "SR2 -> CD-195 (townhouse, 53 UPH / 22 UPA)",
        },
        {
            "title": "Drawings 2024-06-19",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=27468C240625153323164900&folderId=27468C240625145039236695",
        },
    ),
    "DP101447": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "21214 82 Avenue",
            "units_total": 60,
            "unit_mix": {"townhouse": 60},
        },
        {
            "title": "Elevations 2024-06-06",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=52C4947F-4B7C-4294-B699-B3B8BF370C08&folderId=21328C240606101948022946",
        },
    ),
    # --- batch 3a: recent DPs ---
    "DP101375": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "8595 200 Street",
            "number_of_stories": 3,
            "floor_area_ratio": 1.5,
            "parking_vehicle_stalls": 184,
            "zoning_density": "SR-2 -> CD",
        },
        {
            "title": "Development Permit No. 101375 - March 24, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=5683963D-71F4-46E5-9BD3-FA090DE36E47&folderId=62573C221202142449483587",
        },
    ),
    "DP101346": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "4185 208th Street",
            "floor_area": 4086.12,
            "floor_area_unit": "m2",
            "number_of_stories": 3,
            "height_m": 15.85,
            "lot_coverage": 32.3,
            "parking_vehicle_stalls": 150,
            "zoning_density": "C-2",
        },
        {
            "title": "Development Permit No. 101346 - October 21, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=C93E1BFF-5E1E-4F22-B9F7-541CDDE5DA0D&folderId=26357C220725122505150086",
        },
    ),
    "DP101426": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "8576 196th Street",
            "floor_area": 3940.0,
            "floor_area_unit": "m2",
            "lot_area": 8348.2,
            "lot_area_unit": "m2",
            "lot_coverage": 41.0,
            "number_of_stories": 2,
            "height_m": 22.5,
            "parking_vehicle_stalls": 16,
            "parking_bike_stalls": 4,
            "zoning_density": "SR-2 -> M-1A",
        },
        {
            "title": "Site Plan 2023-10-26",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=BF3B8696-43F0-49C9-BECB-BA530FF8721A&folderId=56642C231027100018758383",
        },
    ),
    "DP101428": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20239 84 Avenue",
            "units_total": 100,
            "unit_mix": {"1-bed": 36, "1-bed-den": 16, "2-bed": 26, "2-bed-den": 7, "3-bed": 15},
            "number_of_stories": 6,
            "floor_area": 8033.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 2.8,
            "lot_area": 2870.0,
            "lot_area_unit": "m2",
            "lot_coverage": 48.0,
            "parking_vehicle_stalls": 153,
            "zoning_density": "SR-2 -> CD, FSR 2.8",
        },
        {
            "title": "Report to Council - May 12, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=39BFEE7C-C625-45D8-80E6-EB75AF3F2AAC&folderId=64992C231120143726328209",
        },
    ),
    "DP101361": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20115 80th Avenue",
            "units_total": 188,
            "number_of_stories": 6,
            "floor_area": 16568.5,
            "floor_area_unit": "m2",
            "floor_area_ratio": 2.41,
            "lot_area": 9922.0,
            "lot_area_unit": "m2",
            "lot_coverage": 55.2,
            "parking_vehicle_stalls": 327,
            "parking_bike_stalls": 236,
            "zoning_density": "Apartment Mixed Use (Latimer), FSR 2.41",
        },
        {"title": "RZ100761: Architectural Drawings 2022-11-01", "url": None},
    ),
    "DP101385": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "address": "21498 Old Yale Road",
            "zoning_density": "C-2",
        },
        {
            "title": "Heritage Alteration Permit No. 101385 - July 10, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=0A24C7B7-B48D-4F2D-9C08-98276FCDA3E2&folderId=68214C230109140236178049",
        },
    ),
    "DP101363": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7680 202A Street",
            "units_total": 44,
            "unit_mix": {"townhouse": 44},
            "number_of_stories": 3,
            "floor_area_ratio": 0.9,
            "lot_area": 10105.4,
            "lot_area_unit": "m2",
            "lot_coverage": 29.0,
            "parking_vehicle_stalls": 105,
            "zoning_density": "FSR 0.90, 25 UPA",
        },
        {
            "title": "Revised architectural drawings 31-01-2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=6A1AB7B9-FED4-47DB-A200-07D3691F13A6&folderId=43290C221007110056719934",
        },
    ),
    "DP101350": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20117 84th Avenue",
            "units_total": 273,
            "unit_mix": {"1-bed": 109, "1-bed-den": 56, "2-bed": 75, "3-bed": 24, "studio": 9},
            "number_of_stories": 6,
            "height_m": 22.5,
            "floor_area_ratio": 2.58,
            "lot_area": 9481.5,
            "lot_area_unit": "m2",
            "lot_coverage": 55.5,
            "parking_vehicle_stalls": 405,
            "parking_bike_stalls": 319,
            "zoning_density": "FSR 2.58",
        },
        {
            "title": "Report to Council - January 15, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=18B5C581-9626-4D12-AA44-9B920B3F3C1B&folderId=28447C220803120708878829",
        },
    ),
    "DP101441": (
        {"zoning_density": "CD-157 (industrial/office/daycare, max 22m/4 storeys)"},
        {
            "title": "Report to Council - July 15, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=005B73C7-4FC3-49A7-B766-A7F4175A2A4F&folderId=95070C240319100448662632",
        },
    ),
    "DP101434": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7065 204 Street",
            "units_total": 25,
            "unit_mix": {"townhouse": 25},
            "number_of_stories": 3,
            "lot_area": 4047.0,
            "lot_area_unit": "m2",
            "lot_coverage": 43.7,
            "parking_vehicle_stalls": 55,
            "zoning_density": "SR-2 -> CD, 25 UPA",
        },
        {
            "title": "Elevations - Building 2024-01-09",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=81637C240129104334675971&folderId=76778C240109112710448737",
        },
    ),
    "DP101429": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "27447 56th Avenue",
            "number_of_stories": 1,
            "floor_area": 203.46,
            "floor_area_unit": "m2",
            "lot_area": 12782.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 7,
            "zoning_density": "M-3 Heavy Industrial",
        },
        {
            "title": "Development Permit No. 101429 - January 22, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=9C28FC64-92FB-4F5A-8DB8-5EC17D31095F&folderId=65838C231122161721090882",
        },
    ),
    "DP101431": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26713 56 Avenue",
            "floor_area": 30390.66,
            "floor_area_unit": "m2",
            "lot_area": 51968.41,
            "lot_area_unit": "m2",
            "lot_coverage": 57.0,
            "number_of_stories": 2,
            "height_m": 14.28,
            "parking_vehicle_stalls": 199,
            "parking_bike_stalls": 18,
            "zoning_density": "702 General Industrial -> M-2",
        },
        {
            "title": "Renderings 2023-12-19",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A6998707-CE5F-4154-BCB9-11742AB340A8&folderId=73147C231219122631672047",
        },
    ),
    # --- batch 3b ---
    "DP101440": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7600 Block of 198B Street",
            "units_total": 620,
            "unit_mix": {"apartment": 620},
            "number_of_stories": 6,
            "zoning_density": "CD-178",
        },
        {
            "title": "Report to Council - October 21, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=16B2D8D6-3536-468A-B2EB-B454F6F6ADFF&folderId=90540C240301110126684483",
        },
    ),
    "DP101432": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "19723 88 Avenue",
            "number_of_stories": 6,
            "zoning_density": "-> M-1A (self-storage)",
        },
        {
            "title": "Report to Council - July 15, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=2680A6AF-5520-4BCE-B3C9-8D4071926888&folderId=73843C231221103111385558",
        },
    ),
    "DP101413": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "26574 Gloucester Way",
            "lot_area": 10040.0,
            "lot_area_unit": "m2",
            "lot_coverage": 25.7,
            "height_m": 12.7,
            "parking_vehicle_stalls": 149,
            "zoning_density": "C3 Service Commercial",
        },
        {
            "title": "2026-05-06 Architectural Drawings",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=54576005-D22B-4B35-8C52-F3E4DB250E58&folderId=13152C230602152630882274",
        },
    ),
    "DP101406": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7620 197 Street",
            "units_total": 26,
            "unit_mix": {"townhouse": 26},
            "number_of_stories": 3,
            "lot_area": 8097.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 61,
            "zoning_density": "SR-2 -> CD",
        },
        {
            "title": "Drawings - Architectural 2023-04-20",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A172CDF8-DFED-4E88-994E-5A1C597ECA74&folderId=99303C230419144208036782",
        },
    ),
    "DP101430": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26090 30A Avenue",
            "floor_area": 21776.5,
            "floor_area_unit": "m2",
            "lot_area": 38314.57,
            "lot_area_unit": "m2",
            "number_of_stories": 2,
            "height_m": 14.4,
            "parking_vehicle_stalls": 151,
            "parking_bike_stalls": 13,
            "zoning_density": "703 Heavy Industrial -> M-3",
        },
        {
            "title": "Drawings - Architectural 2023-12-18",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=81637C240129103805568965&folderId=72806C231218114619263986",
        },
    ),
    "DP101415": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20125 81A Avenue",
            "units_total": 16,
            "unit_mix": {"duplex": 16},
            "number_of_stories": 2,
            "height_m": 9.0,
            "zoning_density": "R-CL(SD)",
        },
        {
            "title": "Site Plan 2023-05-05",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=C421124D-C00F-4F45-9BF0-80EF4A2E6FFB&folderId=23595C230704090717836379",
        },
    ),
    "DP101402": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "21588 96 Avenue",
            "zoning_density": "CRD-4 (townhouse, 26.1 UPA)",
        },
        {
            "title": "Report Back to Council - May 12, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=9E117F64-5DE5-4DA6-B62E-D86DEECF0B70&folderId=97921C230414095512987661",
        },
    ),
    "DP101391": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20615 67 Avenue",
            "units_total": 608,
            "unit_mix": {"apartment": 509, "townhouse": 99},
            "number_of_stories": 6,
            "floor_area_ratio": 2.02,
            "zoning_density": "CD-206/CD-207",
        },
        {
            "title": "Report to Council - September 23, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=1BF71C39-01AE-402B-BBAE-68C67FD187DE&folderId=76548C230203100021605092",
        },
    ),
    # --- batch 3c ---
    "DP101439": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "200th Street & 80th Avenue",
            "units_total": 369,
            "number_of_stories": 40,
            "floor_area": 34891.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 4.3,
            "lot_area": 8116.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 821,
            "zoning_density": "FAR 4.30 (residential 2.92 + office 1.03 + commercial 0.35)",
        },
        {
            "title": "Drawings 2024-02-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=C6E5990F-DF90-4738-BAE0-F1AB3F6F1A75&folderId=87737C240221085730364471",
        },
    ),
    "DP101407": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7135 208 Street",
            "units_total": 146,
            "unit_mix": {
                "1-bed": 12,
                "1-bed-den": 95,
                "2-bed": 6,
                "2-bed-den": 16,
                "3-bed": 8,
                "3-bed-den": 9,
            },
            "number_of_stories": 4,
            "floor_area": 11921.0,
            "floor_area_unit": "m2",
            "lot_area": 7474.3,
            "lot_area_unit": "m2",
            "lot_coverage": 44.5,
            "parking_vehicle_stalls": 221,
            "zoning_density": "SR-2 -> CD (NE Gordon Estate, 40-80 UPA)",
        },
        {
            "title": "Drawings 2023-04-20",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=47C9A4CB-5325-4C4B-A093-4F8CFD08582E&folderId=98471C230420153526622359",
        },
    ),
    "DP101422": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7860 197 Street",
            "units_total": 46,
            "unit_mix": {"townhouse": 46},
            "zoning_density": "SR-2 -> CD-190, 23 UPA",
        },
        {"title": "RZ100759: Report to Council - March 25, 2024", "url": None},
    ),
    "DP101349": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "19942 78B Avenue",
            "units_total": 79,
            "unit_mix": {"apartment": 79},
            "number_of_stories": 6,
            "zoning_density": "CD-216 (79 apt + 133 hotel rooms + 1,844 m2 commercial, up to 2.5 FSR)",
        },
        {"title": "RZ100711: Report to Council - July 13, 2026", "url": None},
    ),
    # --- batch 3d ---
    "DP101420": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20370 & 20392 73A Avenue",
            "units_total": 205,
            "unit_mix": {"apartment": 205},
            "number_of_stories": 5,
            "floor_area_ratio": 1.85,
            "zoning_density": "CD-150 (205 apartments, 1.85 FSR, 5 storeys)",
        },
        {"title": "RO100273: Report to Council - January 29, 2024", "url": None},
    ),
    "DP101410": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20792 68 Avenue",
            "units_total": 18,
            "unit_mix": {"townhouse": 18},
            "zoning_density": "CD-201 (18 townhouses)",
        },
        {"title": "RZ100739: Report to Council - March 25, 2024", "url": None},
    ),
    "DP101404": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "19752 80 Avenue",
            "units_total": 55,
            "unit_mix": {"townhouse": 55},
            "zoning_density": "CD-190 (55 townhouses, 15.4 upa)",
        },
        {"title": "RZ100770: Report to Council - April 15, 2024", "url": None},
    ),
    "DP101409": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7538 202A Street",
            "units_total": 49,
            "unit_mix": {"townhouse": 12, "detached": 37},
            "lot_area": 20229.0,
            "lot_area_unit": "m2",
            "zoning_density": "R-CL(SD)/R-CL(A)/R-1A subdivision (49 lots)",
        },
        {"title": "RZ100687: Site Plan 2022-01-12", "url": None},
    ),
    "DP101399": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "CD-190 (townhouses, max 30 upa)",
        },
        {"title": "RZ100617: Report to Council - July 24, 2023", "url": None},
    ),
    "DP101368": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "19819 68 Avenue",
            "units_total": 6,
            "unit_mix": {"detached": 6},
            "zoning_density": "-> R-CL(B) (6 single-family lots)",
        },
        {"title": "RZ100702: Report to Council - May 1, 2023", "url": None},
    ),
    "DP101354": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20120 86 Avenue",
            "units_total": 464,
            "number_of_stories": 39,
            "floor_area_ratio": 2.8,
            "zoning_density": "mixed-use (employment 2.0 / apartment 2.8 / medium 1.9 FSR), Carvolth NP",
        },
        {"title": "RO100239: Report to Council - September 15, 2025", "url": None},
    ),
    # --- batch 2c: recent DP cover/site-plan reads (image fallbacks + dense data sheets) ---
    "DP101438": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "22652 Fraser Highway",
        },
        {
            "title": "Development Permit No. 101438 - October 21, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=3581CCFE-A08C-4C20-9898-E6621E4EF82A&folderId=84072C240206084716047576",
        },
    ),
    "DP101408": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26010 30A Avenue",
            "parking_vehicle_stalls": 51,
            "lot_area": 20100.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> M-2 (truck parking)",
        },
        {
            "title": "Site Plan 2023-04-24",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=0A1B3FE0-138A-42B0-933E-B23440B45BEE&folderId=98471C230421114618306324",
        },
    ),
    "DP101388": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "3111 272 Street",
            "number_of_stories": 6,
        },
        {"title": "RZ100727: 2023-01-20", "url": None},
    ),
    "DP101373": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "5500 Block of 268, 272 and 275 Streets",
            "zoning_density": "RU-1/RU-10 -> CD-185 (Gloucester industrial)",
        },
        {"title": "RO100245: Report to Council - May 29, 2023", "url": None},
    ),
    "DP101437": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20418, 20442 & 20464 76 Avenue",
            "units_total": 9,
            "unit_mix": {"triplex": 9},
        },
        {
            "title": "Drawings - Architectural 2 2024-01-31",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=C0A0D890-79DD-4886-BADB-D3569D4E6F90&folderId=83231C240202085952902555",
        },
    ),
    "DP101433": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7734 197 Street",
        },
        {
            "title": "Drawings - Architectural 2023-12-21",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=81637C240129104143560437&folderId=73843C231221162031004464",
        },
    ),
    "DP101427": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "6677 Willowbrooke Connector",
            "units_total": 50,
            "unit_mix": {"townhouse": 50},
        },
        {
            "title": "Drawings 2023-11-08",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=7568B852-3204-490E-BFA6-35A36C2EE0D1&folderId=61964C231108143608013778",
        },
    ),
    "DP101421": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "3170 196 Street & 19642 32 Avenue",
            "units_total": 18,
            "unit_mix": {"detached": 18},
            "lot_area": 9614.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> R-CL (18 lots)",
        },
        {
            "title": "Site Plan 2023-09-19",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=67F475A7-1709-434D-BA4D-936ADCFD9107&folderId=46112C230919115002870177",
        },
    ),
    "DP101418": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "27527 51A Avenue",
        },
        {
            "title": "Development Permit No. 101418 - January 6, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=326C193F-0E9A-4AAE-88C9-A895C68680D9&folderId=35073C230810101625810962",
        },
    ),
    "DP101401": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7566 197 Street",
            "units_total": 9,
            "unit_mix": {"rowhouse": 5, "semi-detached": 2, "detached": 2},
            "lot_area": 3700.0,
            "lot_area_unit": "m2",
            "zoning_density": "R-1A/R-CL(B)/R-CL(SD)/R-CL(RH)",
        },
        {
            "title": "Report to Council - July 24, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=04B8554C-F2CB-4F1A-9E25-4861CA9CB5CE&folderId=92337C230406121741115055",
        },
    ),
    "DP101400": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "26739 & 26771 24 Avenue",
            "units_total": 32,
            "unit_mix": {"detached": 32},
            "lot_area": 17800.0,
            "lot_area_unit": "m2",
            "zoning_density": "R-1A/R-CL(A) (32 lots)",
        },
        {"title": "RO100249: Report to Council - April 17, 2023", "url": None},
    ),
    "DP101395": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20244 74B Avenue",
            "units_total": 115,
            "unit_mix": {
                "studio": 3,
                "1-bed": 16,
                "1-bed+den": 60,
                "2-bed": 6,
                "2-bed+den": 19,
                "3-bed": 11,
            },
            "number_of_stories": 5,
            "floor_area_ratio": 1.73,
            "lot_area": 8093.97,
            "lot_area_unit": "m2",
            "lot_coverage": 49.0,
            "parking_vehicle_stalls": 193,
            "parking_bike_stalls": 30,
            "zoning_density": "multi-family apartment + 6 CRU",
        },
        {"title": "RO100277: Architectural Drawings 2023-10-18", "url": None},
    ),
    "DP101386": (
        {"address": "2883 264 Street"},
        {
            "title": "Development Permit No. 101386 - March 11, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A268D072-E2D9-4476-AC95-AAFA03050C0C&folderId=71314C230113122842820475",
        },
    ),
    "DP101383": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20481 73A Avenue",
            "units_total": 21,
            "unit_mix": {"detached": 21},
            "zoning_density": "SR-2 -> R-1A (21 lots)",
        },
        {"title": "SA101312: Site Plan 2022-12-15", "url": None},
    ),
    "DP101374": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20545 76 Avenue",
            "units_total": 13,
            "zoning_density": "R-1A/R-CL (13 compact lots)",
        },
        {
            "title": "Development Permit No. 101374 - April 16, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=655ED4A1-763A-4B35-BFF2-667C49FE6150&folderId=61067C221128111134943985",
        },
    ),
    "DP101372": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20600 to 20700 Block of 84 Avenue & 8400 Block of 207A Street",
            "units_total": 28,
            "zoning_density": "R-1A/R-CL(A)/R-CL(B) (Yorkson, 28 lots)",
        },
        {
            "title": "Development Permit No. 101372 - May 9, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A8169DCF-BCF6-4AE7-8CD7-ED68D836E8DE&folderId=58076C221121144709707558",
        },
    ),
    "DP101358": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "27112 56 Avenue",
            "number_of_stories": 2,
        },
        {"title": "RZ100714: Drawings 2023-04-13", "url": None},
    ),
    "DP101347": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "19803 & 19837 86 Avenue",
        },
        {"title": "RZ100710: Drawings 2022-07-27", "url": None},
    ),
    "DP101465": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "number_of_stories": 6,
        },
        {
            "title": "Development Permit No. 101465 - February 24, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=6C0B3E44-753C-47DB-BA19-656F1EEF2F43&folderId=74377C241119120023986669",
        },
    ),
    "DP101466": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "number_of_stories": 6,
        },
        {
            "title": "Report to Council - February 24, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A2CB7363-C7EB-4AD0-94E4-5DA399ADC59B&folderId=74377C241119124723563994",
        },
    ),
    "DP101393": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20418, 20442 & 20464 76 Avenue",
            "units_total": 36,
            "lot_area": 14627.0,
            "lot_area_unit": "m2",
            "zoning_density": "R-1A/R-CL mix (36 lots, 10 UPA)",
        },
        {"title": "RZ100731: Lot Layout 2023-04-13", "url": None},
    ),
    "DP101389": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "21456 80 Avenue",
            "floor_area_ratio": 2.4,
            "lot_area": 3867.0,
            "lot_area_unit": "m2",
            "zoning_density": "CD (High Density Apartment, 2.4 FSR)",
        },
        {"title": "RZ100729: Site Plan", "url": None},
    ),
    "DP101435": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "lot_area": 2024.0,
            "lot_area_unit": "m2",
            "zoning_density": "CD-190 (townhouses, max 30 UPA)",
        },
        {"title": "RZ100690: Report to Council - February 24, 2025", "url": None},
    ),
    "DP101371": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "lot_area": 10000.0,
            "lot_area_unit": "m2",
            "zoning_density": "CD-208 (townhouses, 13-18 UPA)",
        },
        {"title": "RO100246: Report to Council - October 21, 2024", "url": None},
    ),
    "DP101369": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "CD-77 (Sunmark master rezoning, townhouses)",
        },
        {"title": "RO100189: Report to Council - May 29, 2023", "url": None},
    ),
    "DP101365": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "CD-77 (Sunmark master rezoning, townhouses)",
        },
        {"title": "RO100189: Report to Council - May 29, 2023", "url": None},
    ),
    # --- batch 3: older recent DPs (council reports + data sheets + permit instruments) ---
    "DP101334": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26074 30A Avenue",
            "number_of_stories": 2,
            "floor_area": 10570.0,
            "floor_area_unit": "m2",
            "lot_area": 19148.0,
            "lot_area_unit": "m2",
            "lot_coverage": 47.4,
            "height_m": 11.54,
            "parking_vehicle_stalls": 88,
            "parking_bike_stalls": 15,
            "zoning_density": "RU-1 -> M-2 General Industrial",
        },
        {
            "title": "Drawings 2023-02-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=40D2D334-0D11-4B27-85E7-9FF808ADF06D&folderId=05804C220512094248343035",
        },
    ),
    "DP101331": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 38,
            "unit_mix": {"compact-single": 38},
            "number_of_stories": 2,
            "lot_area": 9718.8,
            "lot_area_unit": "m2",
            "zoning_density": "R-CL(SD) (38 compact lots)",
        },
        {
            "title": "Development Permit No. 101331 - June 14, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=9D81B130-3B46-4B78-A56C-193A7AF0AB84&folderId=92943C220404093617585471",
        },
    ),
    "DP101317": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20722 80th Avenue",
            "units_total": 110,
            "unit_mix": {"studio": 15, "2-bed": 85, "3-bed": 10},
            "number_of_stories": 6,
            "floor_area_ratio": 2.18,
            "lot_area": 4047.1,
            "lot_area_unit": "m2",
            "lot_coverage": 53.6,
            "height_m": 25.7,
            "parking_vehicle_stalls": 193,
            "parking_bike_stalls": 138,
            "zoning_density": "CD (271.6 UPH, 6-storey apt over commercial)",
        },
        {
            "title": "Architectural Drawings 2023-03-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=05B311F7-9639-4167-BE92-E07F8DA356EB&folderId=80247C220215153104358516",
        },
    ),
    "DP101314": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "6296 202 Street",
            "number_of_stories": 1,
            "lot_area": 4282.0,
            "lot_area_unit": "m2",
            "zoning_density": "M-2 (mini-storage addition)",
        },
        {
            "title": "Development Permit No. 101314 - December 4, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=EDFBB0EF-E3C4-4B0F-B557-9B4C61BE6B70&folderId=77664C220204145303206311",
        },
    ),
    "DP101344": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "19886 76 Avenue & 7588 198B Street",
            "units_total": 185,
            "number_of_stories": 6,
            "floor_area_ratio": 2.4,
            "lot_area": 7400.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> CD-191 (High Density Mixed Use)",
        },
        {
            "title": "Report to Council - September 11, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=6AADE77C-EB96-421E-B4EE-C35B3FA7E974&folderId=25423C220721123718412528",
        },
    ),
    "DP101312": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "27309 Fraser Highway",
            "units_total": 44,
            "unit_mix": {"1-bed": 5, "1-bed+den": 5, "2-bed": 34},
            "number_of_stories": 6,
            "floor_area_ratio": 2.28,
            "lot_area": 2387.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 88,
            "parking_bike_stalls": 65,
            "zoning_density": "CD (OCP Medium Density Mixed Use, 2 FSR)",
        },
        {
            "title": "Report to Council - June 10, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=F3E11CA7-633B-4450-8C2C-2F7C71BC4699&folderId=74624C220125122514236248",
        },
    ),
    "DP101297": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7687 200 Street",
            "units_total": 242,
            "unit_mix": {"apartment": 242},
            "number_of_stories": 6,
            "lot_area": 9388.6,
            "lot_area_unit": "m2",
            "lot_coverage": 37.6,
            "parking_vehicle_stalls": 296,
            "zoning_density": "SR-2 -> CD (Latimer 12 Lot 3, 242 units, 104 UPA)",
        },
        {
            "title": "Development Permit No. 101297 - April 15, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=2E48EE7C-EFD4-403E-B372-D6E8EFF912DE&folderId=58227C211116145709356648",
        },
    ),
    "DP101296": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "26260 30A Avenue",
            "floor_area": 3624.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.19,
            "lot_area": 19225.72,
            "lot_area_unit": "m2",
            "lot_coverage": 17.59,
            "height_m": 12.0,
            "number_of_stories": 2,
            "parking_vehicle_stalls": 47,
            "zoning_density": "M-2 General Industrial",
        },
        {
            "title": "Building Elevations 2021-11-10",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=29565162-22D5-420B-9BAB-228007B7168D&folderId=56967C211110113414488194",
        },
    ),
    "DP101291": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial", "institutional"],
            "address": "80 Avenue & 216 Street",
            "floor_area": 29048.0,
            "floor_area_unit": "m2",
            "lot_area": 71994.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 1110,
            "zoning_density": "CD-1 (Williams Commercial Gateway)",
        },
        {
            "title": "Architectural Drawings 2022-02-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=01031C230425105034503483&folderId=52432C211026113549273632",
        },
    ),
    "DP101250": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 260,
            "unit_mix": {
                "studio": 7,
                "1-bed": 18,
                "1-bed+den": 81,
                "1-bed+den+2bath": 18,
                "2-bed+den": 42,
                "2-bed+2bath": 76,
                "3-bed": 18,
            },
            "number_of_stories": 6,
            "floor_area_ratio": 2.02,
            "lot_area": 9829.5,
            "lot_area_unit": "m2",
            "lot_coverage": 42.0,
            "parking_vehicle_stalls": 399,
            "zoning_density": "SR-2 -> CD, FSR 2.02",
        },
        {"title": "RO100261: Drawings 2023-06-21", "url": None},
    ),
    "DP101244": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "19925 & 19957 78B Avenue",
            "units_total": 371,
            "number_of_stories": 6,
            "floor_area_ratio": 1.75,
            "lot_area": 17988.87,
            "lot_area_unit": "m2",
            "lot_coverage": 42.9,
            "parking_vehicle_stalls": 644,
            "zoning_density": "SR-2 -> High Density Mixed Use (CD)",
        },
        {"title": "RZ100664: Architectural Drawings 2022-11-25", "url": None},
    ),
    "DP101242": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20306 82 Avenue",
            "units_total": 103,
            "unit_mix": {"townhouse": 103},
            "number_of_stories": 3,
            "lot_area": 19085.0,
            "lot_area_unit": "m2",
            "lot_coverage": 40.11,
            "parking_vehicle_stalls": 234,
            "zoning_density": "SR2 -> CD (21.82 UPA)",
        },
        {
            "title": "ARCHITECTURAL DRAWINGS SEPTEMBER 25,2024.PDF",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=4BF3E1D8-EE3B-47CC-903D-8EFF4F5742B9&folderId=94999C210409112948668730",
        },
    ),
    "DP101270": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "6815 204 Street",
            "units_total": 143,
            "number_of_stories": 4,
            "floor_area_ratio": 1.62,
            "lot_area": 6000.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> CD-212 (Medium Density Apartment)",
        },
        {
            "title": "Report to Council - January 27, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=07F8A1DA-6FFE-4201-B27E-370D97F0F612&folderId=21167C210705153048510976",
        },
    ),
    "DP101194": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 43,
            "zoning_density": "RU-1 -> CD (multi-family)",
        },
        {
            "title": "Report to Council - September 11, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=28D6E23B-0994-4996-A9B0-C82B9E6FF485&folderId=34426C200824091010737541",
        },
    ),
    "DP101190": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "zoning_density": "SR-2 -> CD (mixed-use midrise)",
        },
        {
            "title": "Development Permit No. 101190 - July 7, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=4A45D572-31CD-46F2-90D7-545D3732DB27&folderId=26566C200723153508400513",
        },
    ),
    "DP101180": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "6868 & 6842 202B Street & 20309 68 Avenue",
            "units_total": 100,
            "unit_mix": {"townhouse": 100},
            "number_of_stories": 3,
            "floor_area_ratio": 0.76,
            "lot_area": 20358.0,
            "lot_area_unit": "m2",
            "height_m": 9.0,
            "parking_vehicle_stalls": 221,
            "zoning_density": "SR-2 -> townhouse CD (20.5 UPA)",
        },
        {
            "title": "ELEVATIONS 2024-25-11.PDF",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=5FF3471B-4200-4A71-B61A-AD12CBED5414&folderId=03083C200429092405800503",
        },
    ),
    "DP101225": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20186 & 20212 86 Avenue",
            "units_total": 223,
            "number_of_stories": 6,
            "floor_area_ratio": 2.62,
            "lot_area": 6700.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 326,
            "zoning_density": "SR-2 -> CD-186",
        },
        {
            "title": "Report to Council - July 10, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=01857CC0-7C96-4CDC-8AA8-EA68DF87A3CE&folderId=73405C210120123900212770",
        },
    ),
    "DP101218": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "9140 Glover Road",
            "units_total": 47,
            "zoning_density": "CD-2 (Fort Langley commercial/residential)",
        },
        {
            "title": "Heritage Alteration Permit No. 101218 - July 24, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=3B769BC0-4F2F-46A2-8B2C-0B7BE06ADE02&folderId=69308C210106095158562323",
        },
    ),
    "DP101162": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20276 72B Avenue",
            "units_total": 66,
            "number_of_stories": 4,
            "zoning_density": "SR-2 -> CD (Willoughby condo)",
        },
        {
            "title": "Development Permit No. 101162 - March 7, 2022",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=CB932350-7E0C-4089-80A9-FF0C57AFD91E&folderId=61419C191206153503566488",
        },
    ),
    "DP101161": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20267 72 Avenue",
            "units_total": 66,
            "number_of_stories": 4,
            "zoning_density": "SR-2 -> CD (Willoughby condo)",
        },
        {
            "title": "Development Permit No. 101161 - March 7, 2022",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=631EBD20-A54C-42E5-AAB7-103BF368864F&folderId=61419C191206130017999145",
        },
    ),
    "DP101157": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20148, 20152 & 20180 82 Avenue",
            "units_total": 84,
            "unit_mix": {"semi-detached": 16, "townhouse": 68},
            "number_of_stories": 3,
            "zoning_density": "R-CL(SD) + CD-131",
        },
        {"title": "RO100178: Report to Council - October 26, 2020", "url": None},
    ),
    "DP101143": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "8192 208 Street",
            "number_of_stories": 6,
            "zoning_density": "Unison multi-family apartment (RM)",
        },
        {"title": "DP101466: Report to Council - February 24, 2025", "url": None},
    ),
    "DP101114": (
        {
            "development_class": "mixed",
            "occupancy_types": ["commercial", "residential"],
            "address": "23210 Fraser Highway",
            "units_total": 3,
            "zoning_density": "C-3 Service Commercial (Heritage Revitalization)",
        },
        {
            "title": "Report to Council - July 24, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=E72F7AB3-E18A-4DB4-8F2E-428B7C56B37E&folderId=95887C190528103802263475",
        },
    ),
    "DP101105": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20186 & 20212 86 Avenue",
            "units_total": 223,
            "number_of_stories": 6,
            "floor_area_ratio": 2.62,
            "lot_area": 6700.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 326,
            "zoning_density": "SR-2 -> CD-186",
        },
        {
            "title": "Report to Council - July 10, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=6EEF4E8A-FD40-4CD8-AF71-0D4F7AF7A464&folderId=79085C190402142135426075",
        },
    ),
    "DP101013": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7042 & 7058 204 Street",
            "units_total": 37,
            "unit_mix": {"townhouse": 37},
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-115 (18.6 UPA)",
        },
        {"title": "RZ100532: Report to Council - December 14, 2020", "url": None},
    ),
    "DP100999": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "zoning_density": "CD (Cornerstone Estates mixed-use)",
        },
        {
            "title": "Report to Council - July 24, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=EFE50A77-7F29-4795-A41B-9E3813D9AA15&folderId=33179C180406161827976489",
        },
    ),
    "DP100962": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7000 Block 202B Street",
            "units_total": 87,
            "unit_mix": {"townhouse": 87},
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-148 (16.8 UPA)",
        },
        {"title": "RO100188: Report to Council - June 15, 2020", "url": None},
    ),
    "DP100957": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20350 & 20326 72 Avenue",
            "number_of_stories": 6,
            "lot_area": 7446.0,
            "lot_area_unit": "m2",
            "zoning_density": "SR-2 -> CD (apartment)",
        },
        {"title": "RO100282: Drawings 2024-01-29", "url": None},
    ),
    "DP100911": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20434 72 Avenue",
            "units_total": 27,
            "unit_mix": {"townhouse": 27},
            "number_of_stories": 3,
            "parking_vehicle_stalls": 61,
            "zoning_density": "SR-2 -> CD-154 (15 UPA)",
        },
        {"title": "RZ100493: Report to Council - December 7, 2020", "url": None},
    ),
    "DP101302": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7581 208 Street",
            "units_total": 75,
            "unit_mix": {"townhouse": 75},
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-169 (15 UPA)",
        },
        {"title": "RZ100604: Report to Council - December 13, 2021", "url": None},
    ),
    "DP101226": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7581 208 Street",
            "units_total": 75,
            "unit_mix": {"townhouse": 75},
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-169 (15 UPA)",
        },
        {"title": "RZ100604: Report to Council - December 13, 2021", "url": None},
    ),
    "DP101477": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "19723 88 Avenue",
            "number_of_stories": 6,
            "lot_area": 12400.0,
            "lot_area_unit": "m2",
            "zoning_density": "M-1A Service Industrial (6-storey self-storage)",
        },
        {"title": "RZ100764: Report to Council - July 15, 2024", "url": None},
    ),
    "DP101524": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "3265 264 Street",
            "zoning_density": "fabric storage structures",
        },
        {
            "title": "Site Plan 2026-03-18",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=20581C260318113843588455&folderId=20581C260318111336002267",
        },
    ),
    "DP101520": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "7980 206A Street",
            "floor_area": 393.0,
            "floor_area_unit": "m2",
            "zoning_density": "commercial CRU tenant improvement",
        },
        {
            "title": "Drawings - Architectural 2026-01-07",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=05118C260107093237438122&folderId=05118C260107085838721541",
        },
    ),
    "DP101333": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "19784 & 19808 76 Avenue",
            "units_total": 27,
            "zoning_density": "R-CL(SD)/R-CL(RH) (27 compact lots)",
        },
        {
            "title": "Development Permit No. 101333 - June 12, 2023",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=7F232D02-5F54-4E87-B2B0-F8B267C17913&folderId=99800C220421090302052279",
        },
    ),
    "DP101212": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "CD (cohousing)",
        },
        {
            "title": "Development Permit No. 101212 - July 25, 2022",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=E4F77184-2A6C-4504-B37E-806233265B03&folderId=68035C201224143438514078",
        },
    ),
    "DP101307": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "zoning_density": "CD (Yorkson Block C)",
        },
        {
            "title": "Development Permit No. 101307 - February 12, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=B9C85B8C-632F-4976-87E2-5583B9D79F54&folderId=67898C211222155215468448",
        },
    ),
    "DP101550": (
        {},
        {
            "title": "Drawings - Architectural 2026-10-02",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=96757C261002153329180837&folderId=96757C261002152918908648",
        },
    ),
    "DP101457": (
        {},
        {
            "title": "Elevations - Architectural 2024-09-16",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=9CF02960-3F07-4C92-9D7D-D99D9FC8303D&folderId=51662C240910114923983875",
        },
    ),
    "DP101538": (
        {},
        {
            "title": "Development Permit No. 101538 - August 4, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=659AC5FE-52B2-48EC-85E2-0439307AE860&folderId=64234C260624142022434963",
        },
    ),
    "DP101508": (
        {},
        {
            "title": "Development Permit No. 101508 - September 15, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=F5DD9771-4EA8-4E4C-B776-96C0A3257D44&folderId=66211C250827084242289323",
        },
    ),
    "DP101259": (
        {},
        {
            "title": "Development Permit No. 101259 - December 15, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=C561BEE5-7C2D-4CA3-AF58-BAC210EFA7F7&folderId=07855C210525150329360574",
        },
    ),
    "DP101290": (
        {},
        {
            "title": "Development Permit No. 101290 - July 25, 2022",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=B42C06BA-9309-4767-9535-0BF3950DEF9A&folderId=50849C211020113025323329",
        },
    ),
    "DP101125": (
        {},
        {
            "title": "Development Permit No. 101125 - February 24, 2020",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=0A68A702-B233-42EC-97CA-E98AD9A3DD01&folderId=22715C190806102351755336",
        },
    ),
    "DP101086": (
        {},
        {
            "title": "Development Permit No. 101086 - March 22, 2021",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=399D55A7-5D43-4804-A10F-6E39E7A57126&folderId=66275C190214085320003029",
        },
    ),
    "DP101145": (
        {},
        {
            "title": "Development Permit No. 101145 - July 26, 2021",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=AD0C8148-2BAB-426B-8866-DB78525C8EFF&folderId=48373C191025125322526377",
        },
    ),
    "DP100822": (
        {},
        {
            "title": "Streamside Development Permit No. 100822 - June 26, 2017",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=A3108DFC-4A50-4A05-A1D5-32196DDD7147&folderId=72874C160115090113410000",
        },
    ),
    "DP101081": (
        {},
        {
            "title": "Development Permit No. 101081 - July 15, 2024",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=F6C6F617-C15E-4572-9644-4DBF6DD1D954&folderId=61345C190129143529581310",
        },
    ),
    # --- batch 4: unknown-class rows classified (companions + purpose + ALR/variance reads) ---
    "DV100167": (
        {"development_class": "mixed", "occupancy_types": ["residential", "commercial"]},
        {"title": None, "url": None},
    ),
    "DV100168": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": None, "url": None},
    ),
    "DV100180": (
        {"development_class": "mixed", "occupancy_types": ["residential", "commercial"]},
        {"title": None, "url": None},
    ),
    "DV100182": (
        {"development_class": "mixed", "occupancy_types": ["residential", "commercial"]},
        {"title": None, "url": None},
    ),
    "DV100184": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": None, "url": None},
    ),
    "DV100196": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 5,
            "unit_mix": {"detached": 5},
        },
        {"title": None, "url": None},
    ),
    "DV100198": (
        {"development_class": "industrial", "occupancy_types": ["industrial"]},
        {"title": None, "url": None},
    ),
    "DV100199": (
        {"development_class": "industrial", "occupancy_types": ["industrial"]},
        {"title": None, "url": None},
    ),
    "DV100200": (
        {"development_class": "commercial", "occupancy_types": ["commercial"]},
        {"title": None, "url": None},
    ),
    "DV100207": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": None, "url": None},
    ),
    "DV100209": (
        {"development_class": "mixed", "occupancy_types": ["residential", "commercial"]},
        {"title": None, "url": None},
    ),
    "DV100210": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": None, "url": None},
    ),
    "DV100212": (
        {"development_class": "mixed", "occupancy_types": ["residential", "commercial"]},
        {"title": None, "url": None},
    ),
    "DV100216": (
        {"development_class": "mixed", "occupancy_types": ["residential", "commercial"]},
        {"title": None, "url": None},
    ),
    "DV100218": (
        {"development_class": "industrial", "occupancy_types": ["industrial"]},
        {"title": None, "url": None},
    ),
    "DV100219": (
        {"development_class": "industrial", "occupancy_types": ["industrial"]},
        {"title": None, "url": None},
    ),
    "RZ100700": (
        {
            "development_class": "mixed",
            "occupancy_types": ["commercial", "residential"],
            "zoning_density": "C-3 (Heritage Revitalization - Berry General Store)",
        },
        {"title": None, "url": None},
    ),
    "RO100258": (
        {"development_class": "institutional", "occupancy_types": ["institutional"]},
        {"title": None, "url": None},
    ),
    "SA101371": (
        {"development_class": "industrial", "occupancy_types": ["industrial"]},
        {"title": None, "url": None},
    ),
    "TU000008": (
        {"development_class": "industrial", "occupancy_types": ["industrial"]},
        {"title": None, "url": None},
    ),
    "DP100909": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": None, "url": None},
    ),
    "LP000069": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "zoning_density": "RU-3 (liquor manufacturer event endorsement)",
        },
        {"title": None, "url": None},
    ),
    "LP000072": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "zoning_density": "RU-3 (liquor manufacturer event endorsement)",
        },
        {"title": None, "url": None},
    ),
    "RZ100733": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "zoning_density": "RU-5 (growing-media processing/distribution facility)",
        },
        {"title": None, "url": None},
    ),
    "SA101367": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "zoning_density": "RU-1 (homesite severance subdivision)",
        },
        {"title": None, "url": None},
    ),
    "TT000016": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "zoning_density": "RU-2 (62m telecommunications tower)",
        },
        {"title": None, "url": None},
    ),
    "TT000025": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "zoning_density": "RU-3 (28m telecommunications tower)",
        },
        {"title": None, "url": None},
    ),
    "TU000007": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "zoning_density": "temporary use - vehicle parking",
        },
        {"title": None, "url": None},
    ),
    "TU000015": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "zoning_density": "RU-1 (temporary use - truck parking)",
        },
        {"title": None, "url": None},
    ),
    "AL100405": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "6614 Glover Road",
            "zoning_density": "RU-3 (non-farm use - equipment parking)",
        },
        {"title": None, "url": None},
    ),
    "AL100472": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "27017 20 Avenue",
            "zoning_density": "RU-2 (non-farm use - event/gathering venue)",
        },
        {"title": None, "url": None},
    ),
    "AL100475": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "24550 72 Avenue",
            "zoning_density": "RU-3 (non-farm use - equestrian show park liquor service)",
        },
        {"title": None, "url": None},
    ),
    "AL100478": (
        {
            "development_class": "institutional",
            "occupancy_types": ["institutional"],
            "address": "22311 No. 10 Highway",
            "zoning_density": "RU-3 (non-farm use - private family mausoleum)",
        },
        {"title": None, "url": None},
    ),
    "RZ100750": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "8929 201 Street",
            "number_of_stories": 8,
            "floor_area": 8153.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 1.6,
            "lot_area": 4962.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 213,
            "zoning_density": "CD-48 (8-storey office + ground retail)",
        },
        {"title": None, "url": None},
    ),
    "RZ100765": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "1034 264 Street",
            "lot_area": 18500.0,
            "lot_area_unit": "m2",
            "zoning_density": "RU-2 (film production studio use)",
        },
        {"title": None, "url": None},
    ),
    "SA101406": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20343 & 20387 70 Avenue & 20386 & 20342 70A Avenue",
            "number_of_stories": 6,
            "zoning_density": "CD-189 (air-space parcel subdivision, 6-storey apartments)",
        },
        {"title": None, "url": None},
    ),
    "SA101408": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "6897 Glover Road & 21690 Smith Crescent",
            "lot_area": 39400.0,
            "lot_area_unit": "m2",
            "zoning_density": "CD-167 (2-lot subdivision)",
        },
        {"title": None, "url": None},
    ),
    # --- batch 5: related-application reads (7687 200 St 620 units, Williams Gateway, Carvolth, 8045 198A office) ---
    "DP101299": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 620,
            "unit_mix": {"apartment": 620},
            "number_of_stories": 6,
            "zoning_density": "CD-178 (four 6-storey buildings, Lots 3 & 4)",
        },
        {"title": "RO100224: Report to Council - June 12, 2023", "url": None},
    ),
    "RO100224": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "units_total": 620,
            "unit_mix": {"apartment": 620},
            "number_of_stories": 6,
            "zoning_density": "CD-178 (four 6-storey buildings, Lots 3 & 4)",
        },
        {"title": None, "url": None},
    ),
    "RO100218": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial", "institutional"],
            "address": "80 Avenue & 216 Street",
            "floor_area": 29048.0,
            "floor_area_unit": "m2",
            "lot_area": 71994.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 1110,
            "zoning_density": "CD-1 (Williams Commercial Gateway)",
        },
        {
            "title": "Architectural Drawings 2022-02-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=00724C230424163136000270&folderId=09163C210528142633784375",
        },
    ),
    "RO100279": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20239 84 Avenue & 8400 Block of 202B Street",
            "units_total": 100,
            "unit_mix": {"1-bed": 36, "1-bed+den": 16, "2-bed": 26, "2-bed+den": 7, "3-bed": 15},
            "number_of_stories": 6,
            "floor_area": 8033.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 2.8,
            "lot_area": 2870.0,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 150,
            "zoning_density": "SR-2 -> CD (Carvolth, FSR 2.8)",
        },
        {
            "title": "Report to Council - May 12, 2025",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=C47594DA-C94D-41C7-A9CB-919DA1E04C20&folderId=73518C231220153322361560",
        },
    ),
    "RZ100594": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "8045 198A Street",
            "number_of_stories": 4,
            "floor_area": 3436.0,
            "floor_area_unit": "m2",
            "lot_coverage": 36.0,
            "zoning_density": "SR-2 -> CD-157 (Business Office Park, 2 x 4-storey office)",
        },
        {
            "title": "Report to Council - December 14, 2020",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=CB8D9458-B182-4B64-A0E1-56CF53EB5445&folderId=36492C190920114242373839",
        },
    ),
    "DP101130": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "8045 198A Street",
            "number_of_stories": 4,
            "floor_area": 3436.0,
            "floor_area_unit": "m2",
            "lot_coverage": 36.0,
            "zoning_density": "SR-2 -> CD-157 (Business Office Park: Bldg A 1,479 m2 + Bldg B 1,957 m2)",
        },
        {
            "title": "Report to Council - December 14, 2020",
            "url": None,
        },
    ),
    "DV100166": (
        {"development_class": "commercial", "occupancy_types": ["commercial"]},
        {"title": None, "url": None},
    ),
    # --- batch 6 slice: 10 DP projects read from related RO/RZ/SA reports ---
    "DP101284": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial", "institutional"],
            "address": "19976 & 19970 80th Avenue",
            "units_total": 347,
            "number_of_stories": 7,
            "floor_area_ratio": 1.97,
            "zoning_density": "SR-2 -> CD (High Density Mixed Use, Jericho Crossing Phase 2)",
        },
        {"title": "SA101248: Drawings 2022-11-22", "url": None},
    ),
    "DP101274": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial", "institutional"],
            "address": "22260-22288 48th Avenue",
            "units_total": 75,
            "unit_mix": {"studio": 9, "1-bed": 25, "2-bed": 38, "3-bed": 3},
            "number_of_stories": 4,
            "floor_area": 7339.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 1.97,
            "lot_area": 3731.0,
            "lot_area_unit": "m2",
            "lot_coverage": 52.0,
            "height_m": 18.04,
            "parking_vehicle_stalls": 110,
            "zoning_density": "SR1 -> CD (Apartments/Commercial/Daycare, 201 UPH)",
        },
        {"title": "RO100220: Architectural Drawings 2022-08-18", "url": None},
    ),
    "DP101257": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "9135 King Street",
            "units_total": 22,
            "number_of_stories": 3,
            "floor_area": 2724.83,
            "floor_area_unit": "m2",
            "floor_area_ratio": 1.57,
            "lot_area": 1734.7,
            "lot_area_unit": "m2",
            "lot_coverage": 60.2,
            "parking_vehicle_stalls": 51,
            "zoning_density": "Civic Institutional P1 -> CD (3-storey mixed-use)",
        },
        {"title": "RO100216: Architectural Drawings 2022-02-18", "url": None},
    ),
    "DP101124": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "6834 204 Street & 20441 68 Avenue",
            "units_total": 41,
            "unit_mix": {"townhouse": 41},
            "number_of_stories": 3,
            "floor_area_ratio": 0.84,
            "lot_area": 7419.0,
            "lot_area_unit": "m2",
            "lot_coverage": 45.0,
            "height_m": 12.08,
            "parking_vehicle_stalls": 82,
            "zoning_density": "SR-2 -> CD (41 townhouses, 25.3 UPA)",
        },
        {"title": "RO100198: Report to Council - November 1, 2021", "url": None},
    ),
    "DP101221": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial", "institutional"],
            "address": "19930, 19962 & 19984 76 Avenue",
            "units_total": 302,
            "unit_mix": {"apartment": 302},
            "number_of_stories": 6,
            "floor_area_ratio": 2.84,
            "lot_coverage": 35.0,
            "parking_vehicle_stalls": 535,
            "zoning_density": "RA -> CD (three 6-storey mixed-use buildings, RF70)",
        },
        {"title": "RZ100656: Drawings 2024-06-26", "url": None},
    ),
    "DP101335": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7445 198B Street",
            "units_total": 8,
            "unit_mix": {"detached": 4, "semi-detached": 4},
            "number_of_stories": 2,
            "zoning_density": "SR-2 -> R-CL(A)/R-CL(SD) (8 lots, 8 UPA)",
        },
        {"title": "RZ100585: Report to Council - June 13, 2022", "url": None},
    ),
    "DP101305": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial", "commercial"],
            "address": "8075 198A Street",
            "number_of_stories": 5,
            "zoning_density": "CD-179 (business park: office/warehouse/high-tech + accessory "
            "daycare/restaurant, max 5 storeys)",
        },
        {"title": "RZ100693: Report to Council - March 13, 2023", "url": None},
    ),
    "DP101087": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "6862, 6876 & 6898 204 Street",
            "units_total": 62,
            "unit_mix": {"townhouse": 62},
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD (62 townhouses, 20.3 UPA)",
        },
        {"title": "RZ100570: Report to Council - April 17, 2023", "url": None},
    ),
    "DP101320": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-205 (townhouses, 22 UPA, 3 storeys)",
        },
        {"title": "RO100287: Report to Council - November 4, 2024", "url": None},
    ),
    "DP101179": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7189 208A Street",
            "units_total": 5,
            "unit_mix": {"rowhouse": 5},
            "number_of_stories": 2,
            "parking_vehicle_stalls": 11,
            "zoning_density": "SR-2 -> R-CL(RH) (5 rowhouse lots)",
        },
        {"title": "RZ100593: Report to Council - July 26, 2021", "url": None},
    ),
    # --- batch 7 slice: 10 high-yield projects (re-ranked by data-page score) ---
    "RO100320": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20448, 20460 & 20492 74B Avenue",
            "units_total": 79,
            "unit_mix": {"townhouse": 79},
            "number_of_stories": 3,
            "floor_area_ratio": 0.85,
            "lot_area": 14722.0,
            "lot_area_unit": "m2",
            "lot_coverage": 34.0,
            "height_m": 12.0,
            "parking_vehicle_stalls": 174,
            "zoning_density": "SR-2 -> CD (79 townhouses, 64 UPHA)",
        },
        {
            "title": "Site Plan 2025-11-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=3EE16072-B1A9-42FE-8722-DD64E54D9EFD&folderId=88428C251106141231108237",
        },
    ),
    "RO100316": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "19963 82 Avenue",
            "units_total": 152,
            "unit_mix": {"studio": 10, "1-bed": 98, "2-bed": 27, "3-bed": 17},
            "number_of_stories": 6,
            "floor_area_ratio": 2.74,
            "lot_area": 4057.2,
            "lot_area_unit": "m2",
            "lot_coverage": 55.0,
            "parking_vehicle_stalls": 231,
            "parking_bike_stalls": 152,
            "zoning_density": "SR-2 -> CD (6-storey mixed-use)",
        },
        {
            "title": "Drawings - Architectural 2025-09-09",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=69959C250909120420533994&folderId=69959C250909105145094641",
        },
    ),
    "DP101076": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20145 & 20185 84 Avenue",
            "units_total": 169,
            "unit_mix": {"apartment": 127, "townhouse": 42},
            "number_of_stories": 6,
            "parking_vehicle_stalls": 330,
            "zoning_density": "SR-2 -> CD (127 apartment + 42 stacked townhouse)",
        },
        {"title": "RZ100567: Report to Council - February 13, 2023", "url": None},
    ),
    "RO100332": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7261 & 7317 209A Street & 20891 72 Avenue",
            "units_total": 83,
            "unit_mix": {"townhouse": 83},
            "number_of_stories": 3,
            "lot_area": 13845.0,
            "lot_area_unit": "m2",
            "lot_coverage": 24.4,
            "parking_vehicle_stalls": 185,
            "zoning_density": "SR-2 -> CD (83 townhouses, 24.3 UPA)",
        },
        {
            "title": "Drawings - Architectural 2026-09-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=DC8131BA-E584-4219-920D-C6F0A49F4599&folderId=88345C260904105543196517",
        },
    ),
    "DP101096": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7310 202B Street",
            "units_total": 94,
            "unit_mix": {
                "1-bed": 13,
                "1-bed+den": 37,
                "2-bed": 25,
                "2-bed+den": 8,
                "3-bed": 4,
                "3-bed+den": 7,
            },
            "number_of_stories": 6,
            "floor_area": 8114.24,
            "floor_area_unit": "m2",
            "floor_area_ratio": 2.0,
            "lot_area": 4046.4,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 142,
            "zoning_density": "SR-2 -> CD (94-unit apartment, FSR 2.0)",
        },
        {"title": "RO100180: Drawings 2024-01-16", "url": None},
    ),
    "DV100185": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "9189 196A Street",
            "number_of_stories": 1,
            "floor_area": 3694.0,
            "floor_area_unit": "m2",
            "floor_area_ratio": 0.404,
            "lot_area": 10036.0,
            "lot_area_unit": "m2",
            "lot_coverage": 36.22,
            "height_m": 7.82,
            "parking_vehicle_stalls": 36,
            "zoning_density": "M-2 Industrial (industrial-laundry plant/office addition)",
        },
        {
            "title": "Site Plan 2024-12-20",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=256248B5-0F4D-4FB1-971E-5077F22C503F&folderId=83591C241220100859655087",
        },
    ),
    "RO100318": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "19820 69 Avenue",
            "units_total": 22,
            "unit_mix": {"townhouse": 22},
            "number_of_stories": 3,
            "floor_area_ratio": 0.83,
            "lot_area": 4199.49,
            "lot_area_unit": "m2",
            "lot_coverage": 27.22,
            "parking_vehicle_stalls": 67,
            "zoning_density": "SR-2 -> CD (22 townhouses, 24 UPA)",
        },
        {
            "title": "Site Plan 2025-10-29",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=85889C251029145253371532&folderId=85889C251029135922591401",
        },
    ),
    "RZ100553": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20200 Block of 86 Avenue",
            "units_total": 203,
            "unit_mix": {"studio": 1, "apartment": 202},
            "number_of_stories": 6,
            "floor_area_ratio": 2.7,
            "parking_vehicle_stalls": 318,
            "zoning_density": "SR-2 -> CD-171 (5-6 storey mixed-use, 2.7/2.1 FSR)",
        },
        {
            "title": "Report to Council - May 30, 2022",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=07B64313-688A-469C-A8D9-64EC5E578DB3&folderId=16125C181002114128080713",
        },
    ),
    "RZ100778": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "19942, 19960 & 19986 83 Avenue",
            "units_total": 297,
            "unit_mix": {"1-bed": 121, "2-bed": 140, "3-bed": 36},
            "number_of_stories": 6,
            "floor_area_ratio": 2.97,
            "lot_area": 10813.39,
            "lot_area_unit": "m2",
            "parking_vehicle_stalls": 580,
            "zoning_density": "SR-2 -> High Density Development Zone (three 6-storey mixed-use)",
        },
        {
            "title": "Drawings - Architectural 2024-08-16",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=06856232-F899-4C23-A537-245843D57F02&folderId=46272C240822121013125764",
        },
    ),
    "RO100310": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "8080 & 8126 197 Street",
            "units_total": 71,
            "unit_mix": {"townhouse": 71},
            "number_of_stories": 3,
            "floor_area_ratio": 0.68,
            "lot_area": 16192.53,
            "lot_area_unit": "m2",
            "lot_coverage": 27.36,
            "parking_vehicle_stalls": 156,
            "zoning_density": "SR-2 -> CD (71 townhouses, 22 UPA)",
        },
        {
            "title": "Site Plan - 2025-05-23",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=34230C250526142546072911&folderId=34230C250526135113335364",
        },
    ),
    # --- batch 7b: remaining rich data-pages (re-ranked, read from related reports) ---
    "RO100331": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "23045 48 Avenue",
            "units_total": 9,
            "unit_mix": {"detached": 9},
            "lot_area": 21700.0,
            "lot_area_unit": "m2",
            "zoning_density": "RU-1 -> SR-3 (9 single-family lots)",
        },
        {
            "title": "Site Plan 2026-08-21",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=83651C260821100407897159&folderId=83651C260821095819365148",
        },
    ),
    "RO100158": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7484, 7536 & 7570 200 Street & 7441, 7495 & 7541 202A Street",
            "units_total": 42,
            "unit_mix": {"semi-detached": 42},
            "number_of_stories": 2,
            "zoning_density": "SR-2 -> R-CL(SD) (42 semi-detached in 21 buildings + a rowhouse component)",
        },
        {
            "title": "Report to Council - July 25, 2022",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=C0C03910-AEF0-4452-AF1E-5D56C9B4F189&folderId=94756C180104152306101815",
        },
    ),
    "RO100177": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20148 & 20176 86 Avenue",
            "units_total": 371,
            "floor_area": 37573.0,
            "floor_area_unit": "m2",
            "zoning_density": "CD (Work/Live Flex 2.34 FSR + Medium/High Density Residential, Carvolth)",
        },
        {
            "title": "Report to Council - November 4, 2019",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=19C43E75-5EDB-4430-BB27-25A08D587110&folderId=36879C181119130114845108",
        },
    ),
    "RZ100713": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20156 & 20160 28th Avenue",
            "units_total": 8,
            "unit_mix": {"detached": 8},
            "zoning_density": "SR-2 -> R-1B/R-1C (8 single-family lots)",
        },
        {
            "title": "2026-02-23 Site Plan",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=5AD51E1F-16E7-433A-A1F6-F6A5C0DB90C9&folderId=31943C220818143649789064",
        },
    ),
    "RZ100777": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "3228 & 3258 200 Street",
            "units_total": 452,
            "unit_mix": {"apartment": 452},
            "number_of_stories": 6,
            "floor_area_ratio": 2.3,
            "zoning_density": "SR-2 -> CD-184 (452 apartments + 12,477 m2 commercial/office)",
        },
        {
            "title": "Report to Council - February 23, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=CDF46675-2C06-4DFE-9F16-6A49E9208161&folderId=36448C240722160742401951",
        },
    ),
    "DP100868": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "institutional", "commercial"],
            "address": "7743 & 7787 200 Street",
            "units_total": 196,
            "number_of_stories": 20,
            "floor_area_ratio": 1.25,
            "zoning_density": "CD-118 (Jericho SNP, 7 buildings 4-20 storeys, FSR 1.25)",
        },
        {"title": "RO100157: Report to Council - January 15, 2018", "url": None},
    ),
    "DP100900": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "3134 200 Street",
            "units_total": 82,
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-116 (townhouses, 44 UPHa, 3 storeys)",
        },
        {"title": "RZ100484: PH (with amended DP) Report to Council - March 9, 2020", "url": None},
    ),
    "DP100940": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": "RO100227: Report to Council - June 27, 2022", "url": None},
    ),
    "DP101012": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20443 70 Avenue & 7010 204 Street",
            "units_total": 135,
            "unit_mix": {"apartment": 135},
            "number_of_stories": 5,
            "floor_area": 1028.0,
            "floor_area_unit": "m2",
            "zoning_density": "SR-2 -> CD-151 (135 condos + commercial, 60 UPA)",
        },
        {"title": "RO100203: Report to Council - October 26, 2020", "url": None},
    ),
    "DP101031": (
        {
            "development_class": "mixed",
            "occupancy_types": ["commercial", "residential"],
            "address": "21600 Block of 48 Avenue",
            "units_total": 2,
            "number_of_stories": 2,
            "zoning_density": "SR-1 -> CD-153 (2 buildings, ground commercial + 2 residential units)",
        },
        {"title": "RO100173: Report to Council - October 5, 2020", "url": None},
    ),
    "DP101166": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "19781 80 Avenue",
            "number_of_stories": 4,
            "floor_area": 4304.0,
            "floor_area_unit": "m2",
            "parking_vehicle_stalls": 170,
            "zoning_density": "SR-2 -> CD-157 (4-storey multi-tenant office, ~24 units)",
        },
        {"title": "RZ100612: Report to Council - May 31, 2021", "url": None},
    ),
    "DP101213": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "21247-21397 78 Avenue & 21264-21392 80 Avenue",
            "units_total": 327,
            "zoning_density": "R-1F (7 single-family lots) + CD-172 (townhouse), Williams NP (+ elementary "
            "school site)",
        },
        {"title": "RO100191: Report to Council - June 27, 2022", "url": None},
    ),
    "DP101214": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": "RO100227: Report to Council - June 27, 2022", "url": None},
    ),
    "DP101287": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7100 206 Street",
            "units_total": 83,
            "number_of_stories": 3,
            "zoning_density": "SR-2 -> CD-215 (townhouses, 22 UPA, 3 storeys)",
        },
        {"title": "RO100302: Report to Council - September 15, 2025", "url": None},
    ),
    "DP101310": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial"],
            "address": "8870 196A Street",
            "number_of_stories": 2,
            "floor_area": 5528.0,
            "floor_area_unit": "m2",
            "lot_area": 11786.0,
            "lot_area_unit": "m2",
            "lot_coverage": 44.8,
            "height_m": 11.18,
            "parking_vehicle_stalls": 42,
            "zoning_density": "SR-2 -> CD (industrial warehouse + 2-storey office)",
        },
        {"title": "RZ100697: Site Plan 2023-02-07", "url": None},
    ),
    "RO100265": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "19653 & 19665 Willowbrook Drive",
            "units_total": 1586,
            "unit_mix": {"1-bed": 840, "2-bed": 572, "3-bed": 174},
            "number_of_stories": 44,
            "floor_area_ratio": 7.54,
            "parking_vehicle_stalls": 2500,
            "zoning_density": "Commercial -> CD (Willowtree Towers: 44/40-storey towers over 6-storey "
            "podium)",
        },
        {
            "title": "Drawings 2023-08-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=16FD0025-D49A-441E-B92B-1F32310A09AD&folderId=33715C230804100034366868",
        },
    ),
    "RO100272": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "19677 Willowbrook Drive",
            "units_total": 1065,
            "unit_mix": {"1-bed": 637, "2-bed": 394, "3-bed": 34},
            "number_of_stories": 40,
            "floor_area_ratio": 7.44,
            "parking_vehicle_stalls": 1638,
            "zoning_density": "Commercial -> CD (Willowbrook Towers: 40/34/28-storey towers over 6-storey "
            "podium)",
        },
        {
            "title": "Drawings 2023-08-03",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=5EB58A0D-44B1-4C60-A6BB-516510018A50&folderId=43218C230908090027108161",
        },
    ),
    "RO100292": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial", "institutional"],
            "address": "86 Avenue (Lot 27, between 201 Street & 86 Avenue)",
            "units_total": 600,
            "number_of_stories": 12,
            "floor_area_ratio": 2.87,
            "parking_vehicle_stalls": 550,
            "floor_area": 1050.0,
            "floor_area_unit": "m2",
            "zoning_density": "CD (KKS Nations masterplan: 2 buildings, four 6-12 storey towers)",
        },
        {
            "title": "Architectural Drawings 2025-05-15",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=6105DCD4-C9D3-4E59-B4DF-426D89FED6CD&folderId=20040C240603094654347733",
        },
    ),
    "RO100323": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "8071 197 Street",
            "units_total": 37,
            "unit_mix": {"townhouse": 37},
            "number_of_stories": 3,
            "parking_vehicle_stalls": 83,
            "zoning_density": "SR-2 -> CD (37 townhouses, 17.58 UPA)",
        },
        {
            "title": "Site Plan - 2026-03-10",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=373EDEC8-B083-4F9D-B86E-B2D943D67B92&folderId=25885C260311161733045503",
        },
    ),
    "RO100327": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "2759 200th Street",
            "units_total": 36,
            "zoning_density": "SR-2 -> R-CL(RH2)/CD townhouse (east) + M-1A/M1-B employment (west), 20 UPA",
        },
        {
            "title": "Site Plan - 2026-06-09",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=58870C260610154053824076&folderId=58870C260610145453310427",
        },
    ),
    "RZ100787": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20731 72 Avenue",
            "units_total": 218,
            "unit_mix": {"apartment": 140, "townhouse": 33},
            "zoning_density": "SR-2 -> CD (Low Density Mixed Use 45 + High Density Apartment 140 + RM-3 "
            "Townhouse 33)",
        },
        {
            "title": "Site Plan 2025-03-17",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=6AFBDE12-8ED8-4B53-8F5C-CA6463D8C4D2&folderId=07432C250317095816683112",
        },
    ),
    "RZ100790": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "7410 & 7438 208 Street & 7423, 7428 & 7400 Block 209A Street",
            "number_of_stories": 6,
            "zoning_density": "SR-2 -> CD-220 (apartment, 2.4 FSR, 6 storeys) + CD-131 (townhouse, 22 UPA)",
        },
        {
            "title": "Report to Council - July 13, 2026",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=1B524284-B6D2-40D2-9382-4F6E55EB3BC8&folderId=20673C250417154233293397",
        },
    ),
    "DP100896": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "3321 200 Street & 19882 & 19944 33A Avenue",
            "units_total": 97,
            "unit_mix": {"townhouse": 37, "apartment": 60},
            "floor_area": 3157.0,
            "floor_area_unit": "m2",
            "zoning_density": "SR-2 -> C-2 + CD-182 (37 townhouses + 60 apartments + commercial)",
        },
        {"title": "RZ100478: Report to Council - June 12, 2023", "url": None},
    ),
    "DP100930": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "2600 Block of 268 Street",
            "units_total": 70,
            "unit_mix": {"detached": 25, "townhouse": 45},
            "zoning_density": "SR-2 -> CD-115/R-1B/R-CL(A) (25 single-family lots + 45 townhouses)",
        },
        {"title": "RO100254: Report to Council - May 29, 2023", "url": None},
    ),
    "RO100142": (
        {
            "development_class": "industrial",
            "occupancy_types": ["industrial", "commercial"],
            "address": "24389 Fraser Highway & 3939 244 Street",
            "number_of_stories": 3,
            "lot_coverage": 13.0,
            "parking_vehicle_stalls": 119,
            "zoning_density": "CD-116 (office + industrial, tilt-up)",
        },
        {
            "title": "Report to Council - July 10, 2017",
            "url": "https://e-connect.tol.ca/OurCity/Prospero/FileDownload.aspx?fileId=2B4B5C2C-AFF1-40CD-8D03-B85322AE72E0&folderId=62608C161101143650940000",
        },
    ),
    # --- blank-address fills: civic address read from related-permit docs ---
    "RZ100708": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20800 40th Avenue",
            "units_total": 16,
            "unit_mix": {"townhouse": 16},
            "number_of_stories": 3,
            "floor_area_ratio": 0.86,
            "lot_area": 2842.46,
            "lot_area_unit": "m2",
            "lot_coverage": 39.0,
            "parking_vehicle_stalls": 35,
            "zoning_density": "SR-1 -> CD (16 townhouses, 22.78 UPA)",
        },
        {"title": "Site Plan 2022-08-08", "url": None},
    ),
    "DP101339": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20800 40th Avenue",
            "units_total": 16,
            "unit_mix": {"townhouse": 16},
            "number_of_stories": 3,
            "floor_area_ratio": 0.86,
            "lot_area": 2842.46,
            "lot_area_unit": "m2",
            "lot_coverage": 39.0,
            "parking_vehicle_stalls": 35,
            "zoning_density": "SR-1 -> CD (16 townhouses, 22.78 UPA)",
        },
        {"title": None, "url": None},
    ),
    "DV100146": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "20800 40th Avenue",
            # A variance carries no program of its own: null the unit count the prose mentions
            # ("16-townhouse") so it stays thin and folds into the townhouse project at this address.
            "units_total": None,
            "unit_mix": None,
            "zoning_density": "SR-1 -> CD (16-townhouse Sandhill Brookswood development)",
        },
        {"title": None, "url": None},
    ),
    "DP101269": (
        {
            "development_class": "residential",
            "occupancy_types": ["residential"],
            "address": "21213 80 Avenue",
            "zoning_density": "SR-2 -> CD (Williams NP Phase 1 rowhouse/townhouse)",
        },
        {"title": "RO100238: Third Reading Report to Council - July 24, 2023", "url": None},
    ),
    "RO100325": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial"],
            "address": "8500 200th Street",
            # Same single-storey Wesgroup food store as the companion DP101531 (floor 2,100 m2): set
            # the storey count so the two filings share a program signature and consolidate into one.
            "number_of_stories": 1,
        },
        {"title": "Elevations 2026-05-12", "url": None},
    ),
    "DP101048": (
        {
            "development_class": "mixed",
            "occupancy_types": ["residential", "commercial"],
            "address": "20200 Block of 86 Avenue",
        },
        {"title": "RZ100553: Report to Council - May 30, 2022", "url": None},
    ),
    "SA101255": (
        {
            "development_class": "commercial",
            "occupancy_types": ["commercial", "institutional"],
            "address": "80 Avenue & 216 Street",
        },
        {"title": "RO100218: Architectural Drawings 2022-02-15", "url": None},
    ),
}

NOTES = {
    "DP101536": "Project data (Penmat Mana JV, 200 St): two-lot townhouse development - Lot A 40 TH units (lot 6,553.7 m2, parking 80+8) + Lot B 85 TH units (lot 13,808.4 m2, parking 170+17) = 125 units, 3 storeys, density 17.9 UPA; developable area 28,335 m2; SR-2 -> CD; EV charging per unit.",
    "DP101530": "Project data (7518 & 7540 208 St, BFA Studio): rezone SR-2 -> CD-221 for 211 seniors' housing rental units (<=70 m2 each, 23 up to 75 m2), 6 storeys, max density 3.25 FSR; parking 69 (1 per 4 units + 1 per 3 employees, 34% small car). Bylaw 6248.",
    "DP101510": "Project data (Wesmont, 7620 197 St): 26-unit townhouse development in 6 buildings (Phase 1: 14, Phase 2: 12), 3 storeys; floor area 38,787 sq ft excl garages (3,603 m2), incl garages 50,269 sq ft; gross lot 87,153 sq ft (8,090 m2, net 5,300), coverage 42.3%; parking 56 resident (52 garage + 4 surface) + 6 visitor; SR-2 -> CD, 13.3 UPA.",
    "DP101534": "Unit summary (Yorkson Meadows, 207a St & 84 Ave, Qualico): fee-simple rowhouse development, 28 units in 10 buildings (3x 4-plex, 2x 3-plex, 5x duplex); R-CL(RH) 4-plex/3-plex + R-CL(SD) duplex lots; 2 covered stalls/unit; combined lot 6,287.4 m2; max height 9.0m.",
    "DP101529": "Site info (3983 200A St): two 2-storey + in-ground basement duplexes (Lot A 11,544 sq ft + Lot B 9,195 sq ft), 4 dwelling units; each duplex floor area 7,131 sq ft (basement 2,025 + main 2,039 + upper 2,209 + garage 858); R-1E, site coverage 35%.",
    "DP101505": "Development data (20431 & 20453 74B Ave, Flat Architecture): 116-unit townhouse development (all 3-bedroom) in 23 buildings; FAR 0.84 gross / 0.96 net, GFA 177,038.99 sq ft (16,447 m2); gross site 4.82 ac (19,485.84 m2), coverage 34.72%; parking 232 resident + 24 visitor; indoor amenity 220.55 m2 + outdoor 565.22 m2.",
    "DP101504": "Statistics (Athenry Gate, 20861 83rd Ave, collabor8): 164-unit apartment development in three 4-storey buildings (R1 existing 66 + R2 62 + R3 38 new); GFA proposed 153,217 sq ft (14,234 m2); lot 2.08 ac (8,430 m2), coverage 44.14%; 79 units/acre.",
    "DP101543": "Project data (9126 Church St, Kapoor): two single-family houses (A & B, 2,251 sq ft each) with secondary suites (4 dwelling units) on an 809.37 m2 lot; R-1E; per house main 1,055 + upper 1,196 + basement 903 + suite 592 sq ft.",
    "DP101544": "Project data (20126 82 Ave, GMR Braich Homes): proposed duplex (Unit 101 + Unit 102), 3 storeys each; total floor area 5,955 sq ft (553.2 m2); lot 5,702 sq ft (529.73 m2); R-CL(A); possible secondary suites (suite parking shown).",
    "DP101541": "Project data (4945 224 St, Bakerview): fourplex - 4 townhouse units (each 3-bed 2-bath, 1,021.17 sq ft); total floor area 7,935.36 sq ft (737.2 m2); site 1,299.8 m2 (net 1,295.14), coverage 33%; building height 6.74m; SR-1.",
    "DP101546": "Subdivision plan (Lot 9 Section 22 Tp 8, 74A Ave): four single-family lots - Lot 1 365.1 m2, Lot 2 466.3 m2, Lot 3 466.4 m2, Lot 4 478.7 m2.",
    "DP101517": "Site plan (6331/6336A 264 St, Wicklane Homes): a single-family house on Lot A 21,168 sq ft (1,966.6 m2); main floor 3,700 sq ft + second 868 sq ft + garage 805 sq ft; lot coverage 33% (7,117 sq ft).",
    "DP101535": "Building elevations (Unison, 20882 82 Ave, Isle of Mann Property Group): a 6-storey apartment building (rezoning), building height ~23.66m; unit count not on the elevation sheet.",
    "DP101518": "Subdivision (3400 block 210 St / 210 St & 34 Ave, Read and Lee / Parkhurst, HUB Engineering): create 60 small-scale multi-unit housing lots of ~464.5 m2 each (combined ~27,870 m2), Brookswood-Fernridge/Rinn NP, 13.0m modified road standard; $1,174,260 amenity contribution; 335 replacement trees. Lot data read from the related rezoning RZ100782's site plan (DP101518 has no own documents).",
    "DP101519": "Subdivision (19811/19831/19853 32 Ave, HUB Engineering): create 37 small-scale multi-unit housing lots (6 on 32 Ave, 7 on 33 Ave, 6 on 33A Ave, 18 on 198A St), Brookswood-Fernridge SSMUH 3 / Booth NP; lots 628-648 m2; $724,127 amenity contribution.",
    "DP101533": "Building elevations (19905/19889 32 Ave & 19866 33A Ave, Percept / Common Ground): a multi-building rowhouse development - Building 1 is a 3-unit, 3-storey block (Unit B1/B/B2), building height 32'-8\" (9.96m).",
    "DP101512": "Townhouse development (7922 197 St, ParaMorph): 3-storey townhouses (rendering only on this sheet; unit count not shown).",
    "DP101511": "Apartment development (8360 201 St, ParaMorph, Building 2): rezoning application; indoor amenity 218 m2; main-floor plan only on this sheet (total unit count not shown).",
    "AL100463": "OCP Amendment + Rezoning + ALC (21952 44A Ave): 3.4 ha (8.4 ac) site - north 0.45 ha rezoned SR-1 -> R-1D for single-family (Murrayville CP Single Family One, max 4 UPA, min lot 930 m2), south 2.95 ha remains RU-1/ALR.",
    "AL100480": "Preliminary site layout (3437 264 St, Keywest Asphalt): ALR non-farm use for an equipment storage area (3.47 ac / 1.41 ha) on a 5.59 ac (2.26 ha) RU-3 property, plus a future tree farm; RGS/OCP Industrial, Rural Plan Agricultural. No buildings proposed.",
    "DP101513": "Project data (20056 40A Ave, Jas Oberoi Group): a 1-storey C-2 commercial building (retail/office, 3 units: 147.73 + 147.73 + 136.35 m2); gross floor area 462.80 m2, footprint 462.80 m2; FAR 0.43 (max 1.0); lot coverage 43% (variance from 40%); height 4.57m; 12 parking.",
    "DP101506": "Project data (8740 & 8750 204 St, Pacific Reach): Holiday Inn / Candlewood Suites extended-stay hotel addition - 84 new suites (91 existing); 5 storeys, height 17.20m; GFA 52,588 sq ft (4,885.6 m2), FSR 0.48; lot 10,215.9 m2, total coverage 25.76%; C-3.",
    "DP101548": "Bylaw information (Caliber Office, 8112 199 St, Site Lines): a 4-storey office building (88% office + 12% retail commercial); GFA 3,414 m2 (L1 414 + L2-L4 1,000 each); site 4,300 m2, coverage 24%; height 18m; 116 vehicle + 8 bike stalls, 1 loading; CD-57 (4.1).",
    "DP101527": "Project info (Existing HBC Box Redemise, 19705 Fraser Hwy, MCM/QuadReal): renovation/redemise of the existing Hudson's Bay big-box (Willowbrook) into retail (Group E mercantile + A2 assembly); existing/renovation area 11,391 m2 (122,600 sq ft); 2 storeys; C-1. The lot (152,752.6 m2) is the whole mall site.",
    "DP101531": "Project stats (8500 200th Street, Wesgroup Proposed Food Store, MGBA): a single-storey grocery store, 22,600 sq ft (2,100 m2); site 7,028 m2 (75,645 sq ft), coverage 30%; FAR 0.30 (permitted 2.50); building height 8.16 m; parking 69 stalls (required 60) + 2 loading; rezone C-18 Carvolth Business Park -> C-1 Regional Commercial. Address, height and parking read from the DP.002 Site Stats sheet (the cover and site plan omit them).",
    "DP101540": "Project data (26129 31B Ave, Ace Tank Services): an M-2 industrial building; GFA 895.9 m2 (main 733.5 + mezzanine 50.7 + second floor 111.7), building area 733.6 m2; site 6,899.6 m2, coverage 10.6%, FAR 0.13; height 9.4m; 12 vehicle + 6 bike stalls, 1 loading.",
    "DP101523": "Project data (Timbermart, 26680 Gloucester Way, Martini): tenant improvement of an existing M-2A building - warehouse (Group F2) 39,080 sq ft + office (Group D) 3,680 sq ft = 42,760 sq ft (3,973 m2); building area 39,440.6 sq ft; 1 storey; 35 parking.",
    "DP101521": "Project data (S & R Sawmills, 19791 101 Ave): DVP + DP for a 1-storey new 'D-Mill' building (1,821 m2 / 19,601 sq ft) added to the existing industrial sawmill complex; building height 13.44m (variance from 12m); Heavy Industrial M-5/M-3/M-4; Fraser River streamside (17.6m from top of bank).",
    "DP101542": "Bylaw information (Aldergrove Industrial Park South, 260 St / 28 Ave, Planworks): rezone SR-2 -> M-2 for an industrial warehouse + office (Building 1); GFA 25,573.4 m2 (1st: warehouse 22,647 + office 1,449.3 + services 27.9; 2nd: office 1,449.3); net site 23.0 ac (9.3 ha); height 15.65m (variance); 213 parking, 14 secure + 6 visitor bike; M-2. Concurrent with RZ100805 + DV100216.",
    "DP101507": "Site data (Pannu North Industrial Building, 26477 56 Avenue, Architecture Panel / Orion): a multi-tenant industrial building - 9 warehouse units (each a main floor + mezzanine, 2 storeys, 2nd-floor office); footprint 85,085 sq ft (7,905 m2), mezzanine 17,350 sq ft, total floor area 102,435 sq ft (9,517 m2), FAR 0.50; lot ~206,274 sq ft (19,164 m2), site coverage 41.2%; parking 100 (3 accessible, 20 small-car), 21 loading, 8 bike; M-2. Read from the A-1.0 Site Plan (the cover omits it).",
    "DP101516": "Project data (TRIO, 3110 271 St, Aldergrove): a commercial daycare facility (Group children's daycare), 1,435.50 sq ft (133.36 m2); site 564.34 m2, coverage 38.71%; height 10.23m; 9 parking + 1 drop-off; C-2.",
    "DP101525": "Project data (Langley Temple, 3180 208 St, Kumar Architecture): a 2-storey temple (main 1,110 + second 1,110 = 2,220 m2 / 24,000 sq ft); site 9,267.3 m2 (2.29 ac), coverage 12%; FAR 0.24; parking 117 (31 grade + 86 parkade); SR-2 -> P-1 Civic Institutional.",
    "DP101514": "Zoning synopsis (Sparkling Stars Learning Center, 3136 271 St): a daycare/childcare center (44 children + 7 staff, rooftop playground); floor area 380.8 m2 (1st 146.1 + 2nd 199.4 + roof 35.3); site 563 m2; 9 parking; C-2.",
    "DP101509": "Cover sheet (6810 Glover Rd, David Danyluck): a commercial building + daycare (mixed use), 2 storeys; site 0.321 ha (3,209 m2), coverage 29% (931 m2 roof); height 11.0m; C-2B.",
    "DP101515": "Site plan (Fort Langley Mixed Use Project, 23155 96 Ave, Site Lines): two 4-storey residential-over-commercial buildings + a heritage building; West site 68 units (studio 3 / 1-bed 16 / 2-bed 39 / 3-bed 10, FSR 1.86, 174 parking) + East site 45 units (studio 6 / 1-bed 15 / 2-bed 22 / 3-bed 2, FSR 1.64, 132 parking) = 113 units; height 13.78m.",
    "DP101476": "Project data (Gloucester West, 26477 56 Ave / 5670 264 St, TKA+D): 4 industrial warehouse units (101: 7,554 / 102: 12,303 / 103: 12,357 / 104: 13,024 m2), total 45,238 m2; FSR 0.62; site 73,064 m2, coverage 60%; height 14.5m (variance from 12m); parking 289, loading 75 (71 dock + 4 grade), 32 bike; RU-1 -> M-2.",
    "DP101454": "Project data (Apcon 200 St Residential Dev, 8146 200th Street, Keystone): 245 units in two 6-storey buildings (A 117 + B 128); FSR 2.5 (Lot A 2.56, Lot B 2.40); site gross 11,465 m2 (net 8,491); gross buildable 34,607 m2 (incl parkade); childcare amenity 250 m2, 25 adaptable; SR-2 -> CD.",
    "DP101462": "Site reconciliation ('Atlas' townhome dev, Lot B, 7320 & 7290 208th Street, Focus): 22 townhouse units, 3 storeys; floor area 32,990 sq ft excl garages (3,064.9 m2); gross lot 4,561.4 m2 (net 3,157.5), coverage 47.7%; parking 51 (44 garage + 7 surface); SR-2 -> CD (16-22 UPA).",
    "DP101498": "Project synopsis (Glover Rd Commercial Buildings, 9181/9205/9215 Glover Road, Site Lines): 3 buildings - A heritage 1,145 sq ft (106 m2), B retail 12,014 sq ft, C restaurant 2,683 sq ft (249 m2); total 18,046 sq ft (1,677 m2); site 4,457 m2, coverage 37.6%; 41 parking; C-2.",
    "DP101497": "DP report (Randhawa, 27251 30 Avenue, Architecture Panel): a 3-storey mixed-use building - 5 apartment units + a 208.3 m2 (2,241 sq ft) group children's daycare; site 737.6 m2 (7,940 sq ft); C-2, Aldergrove Core Low Density Residential (1.1 FSR).",
    "DP101481": "Project data (20371 27 Avenue, Lot 59, MARQ): a duplex with secondary suites (Unit 1 + Unit 2, each + suite = 4 dwelling units); total floor area 3,414.50 sq ft; lot 690 m2 (7,435 sq ft); height 9.0m; R-1D.",
    "DP101480": "Zoning reconciliation (20275/20311 32nd Avenue, Cre8): 62-unit townhouse development, density 21.1 UPA; gross site 128,434.64 sq ft (11,932 m2), coverage 34.93%; parking provided 137 (124 in-unit + 13 visitor); SR-2 -> CD (Brookswood/Fernridge).",
    "DP101464": "Project data (BC Builds - Aldergrove, 27214 Fraser Hwy, Site Lines): a 6-storey mixed-use (residential over commercial), 128 units; FSR 1.00; CD-192; parking 241 residential + 13 commercial, 128 bike.",
    "DP101446": "Project data (Zenterra 202 Street Residential Dev, 7950 202A Street, Keystone): 517 units in four 6-storey buildings (A 117 + B 134 + C 138 + D 128); FSR 2.5, GFA 425,937 sq ft (39,570 m2); site gross 18,500 m2 (net 15,847), coverage 46.6%; gross buildable 66,298 m2 incl parkade; CD-194.",
    "DP101471": "Edgar Multi-Family Dev (7883 199 Street, Keystone): 11 townhouse units, 3 storeys; CD-96.",
    "DP101455": "Dhaliwal Res Development (20287 36A Avenue, Keystone): an apartment building (Bldg A, ~12 units, FSR 1.01) + townhouses (Bldg B-G, ~38 units, FSR 0.40); CD. Unit split read from a dense data sheet.",
    "DP101502": "Project data (21528 Old Yale Road, Christophe Massac): a duplex (+ accessory suites), 2 storeys; floor area 3,862 sq ft (358.8 m2); lot 629 m2 (6,771.5 sq ft), coverage 34.36%; height 8.28m; SK-1.",
    "DP101470": "Proposed Multi Family Development (20390 72 Avenue, DF Architecture / IOM): gross site 1.77 ac (7,151.73 m2), net 1.35 ac (5,456.44); SR-2 -> CD. Unit count not on the cover sheet.",
    "DP101503": "Heritage Alteration Permit (9167 Glover Road, Fort Langley Community Improvement Society): an assembly use in the Fort Langley Heritage Conservation Area; parking varied 31 -> 27.",
    "DP101484": "Hald House Heritage Alteration Permit (Fort Langley waterfront, MZA Architects): a residential heritage building.",
    "DP101474": "North Arc Multi-Family (7242 208 Street, Keystone): a multi-family development, FSR 2.76. Full unit/area data on a dense rotated sheet.",
    "DP101467": "'204 Street Townhouses' (6920 204 Street, Ciccozzi): 31 townhouse units (18 four-bed + 13 three-bed), 3 storeys; FAR 0.67, 43,621.99 sq ft (4,052.62 m2); gross site 6,047 m2 (net 5,196.6), coverage 36.4%; parking 68 (62 resident + 6 visitor); SR-2 -> CD (20.8 UPA).",
    "DP101458": "6-unit townhouse development (26700 30a Avenue, Tanglebank / Nerdy Architect), Bldg A + B; floor area 15,172 sq ft (1,409.5 m2); gross lot 2,513.95 m2 (net 2,207.65), coverage 29%; height 9.0m, 2.5 storeys; RM-1.",
    "DP101463": "BC Builds - Willoughby (20230 72B Avenue, Site Lines): a 118-unit 6-storey residential building (1-bed 67 / 2-bed 37 / 3-bed 14); FSR 2.38, GFA 8,673.67 m2; lot 3,640 m2, coverage 46%; parking 177, 118 bike lockers; CD-193.",
    "DP101452": "Building 6 apartment (7510/7488 198B Street, Hwang / MARQ): 131 units, 6 storeys; GFA 115,076.27 sq ft (10,690 m2); unit mix studio 1 / 1-bed 83 / 2-bed 27 / 3-bed 16; parking 140, 110 bike. Part of the RO100295 master development.",
    "DP101445": "Heath South townhouse development (6982 206 Street, Focus): 83 units in 21 buildings (Area 1: 65, Area 2: 9, Area 3: 9), 3 storeys; floor area 119,759 sq ft excl garages (11,126 m2); gross lot 20,235 m2 (net 13,842), coverage 37.4%; parking 166 resident + 17 visitor; SR-2 -> CD.",
    "DP101444": "Rezoning to CD-190 for ~37 townhouse units (47.9 uph / 19.4 upa), 7838 197 Street, Latimer NP (read from the RZ100619 report).",
    "DP101442": "Zenterra Latimer Residential Development (Lot 1, 198 Street, Focus): 62 townhouse units; CD (Lot 2 superseded).",
    "DP101468": "Rezoning to CD-214 for 40 townhouse units in 3-storey buildings (21 UPA); 20354 71A Avenue & 7091 204 Street, Willoughby (read from the RO100304 report).",
    "DP101472": "Rezoning (RO100269): Medium Density Apartment (2.5 FSR) + Townhouse (22 UPA), max 6 storeys, CD; Latimer NP.",
    "DP101500": "Report to Council (5115 214 Street, Bains/Purewal): rezone 0.42 ha to SR-3, streamside DP + DVP to subdivide into 2 residential lots; Murrayville CP.",
    "DP101449": "Proposed subdivision (8508 206 Street, Pollyco): 14 single-family lots (R-CL(A) x10 + R-CL(B) x4); gross site 2.0 ac, 7 UPA; SR-2 -> R-CL.",
    "DP101499": "Manorhome/fourplex (20996 83A Avenue, Amar Properties): 4 units; lot 9,151 sq ft (850.1 m2); RCL-MH.",
    "DP101495": "Industrial truck-parking yard (19759 28th Avenue): 33 truck + 10 passenger spots, 6 bike; site 8,383 m2; existing building as office + existing house.",
    "DP101473": "SSMUH subdivision (Lot 31 - 204 Street, Centras): 23 lots; gross site 18,946 m2 (4.68 ac).",
    "DP101479": "Streamside DP (19700 Block of 84 Avenue, Khela): modify the SPEA of Latimer Creek to accommodate a 110 m2 (1,184 sq ft) mobile home pad + septic field.",
    "DP101450": "Emory Condo (StreetSide/Qualico, 20340/20360/20388 80 Ave & 7849 204 St): 5 apartment buildings, 6 storeys each (unit count on the project-stats sheet).",
    "DP101451": "Emory Townhomes (StreetSide/Qualico, Ekistics; 20315/20323 78 Ave, 20320/20340 80 Ave, 7831/7843/7849 204 St): 157 townhouse units (B 58 + C 91 + G 8) in 35 three-storey buildings; FSR 0.8, net floor 244,520 sq ft (22,716.73 m2); lot 29,394.99 m2 (316,405 sq ft), coverage 38%; parking 346 (314 resident + 32 visitor), 12 bike; SR2 -> CD-195 (companion to the Emory Condo DP101450).",
    "DP101447": "60-unit townhouse development (21214 82 Avenue, Flat Architecture): 14 buildings; Willoughby.",
    "DP101488": "McBride Court (8840 210th Street, PATH): 4 single-family homes (Buildings 1-4).",
    "DP101482": "7278 208 Street Mixed-Use Development (Architecture Panel): a 6-storey mixed-use building (residential over ground-floor commercial).",
    "DP101492": "JOLT EV charging station install (9292 200th Street): a charging stall + cabinet at an existing commercial building (no new building program).",
    "DP101375": "Latimer Grove Lot A townhomes (8595 200 Street, North Arc / Ciccozzi): 14 buildings; FAR 1.5; 184 parking; SR-2 -> CD.",
    "DP101346": "Eastwood Corner Commercial (4185 208th Street, Aldergrove, Martini/Keystone): 3-storey commercial (retail/office) over parkade; GFA 43,987 sq ft (4,086.12 m2); height 15.85m; coverage 32.3%; 150 parking; C-2.",
    "DP101426": "Industrial cold-storage/warehouse (8576 196th Street, Pacific West): freezer+cooler + dry goods + staging, total 43,710 sq ft (3,940 m2); site 8,348.2 m2, coverage 41%; height 22.5m (variance from 12m); 16 parking, 4 bike, 4 loading; SR-2 -> M-1A.",
    "DP101428": "100-unit 6-storey apartment (20239 84 Avenue, 84 Carvolth Holding, F. Adab): mix 36 one-bed / 16 one-bed+den / 26 two-bed / 7 two-bed+den / 15 three-bed; FSR 2.8, GFA 86,476 sq ft (8,033 m2); lot 2,870 m2, coverage 48%; parking 150 + 3 visitor, 10 adaptable; SR-2 -> CD.",
    "DP101361": "188-unit mixed-use (20115 80th Avenue, Lovick Scott), 2 buildings (A 112 + B 76 units), 6 storeys, ground-floor commercial; FSR 2.41, GFA 178,343 sq ft (16,568.5 m2); site gross 9,922 m2 (net 6,880), coverage 55.2%; parking 327, 236 bike; Latimer NP.",
    "DP101385": "Murrayville Academy gym expansion (21498 Old Yale Road, IBI / Edward Tung): a proposed gymnasium (Bldg B, assembly) + soccer field + playground at the existing private school; C-2. (Institutional/school.)",
    "DP101363": "44-unit townhouse development (7680 202A Street, Kasian): FSR 0.90 net; gross site 2.50 ac (10,105.4 m2), net 7,096.04, coverage 29%; parking 105 (94 units + 11 visitor); 25 UPA net; amenity 366.72 m2.",
    "DP101350": "Azure Grove multi-family (20117 84th Avenue, WCA): 273 units (109 one-bed / 56 one-bed+den / 49 two-bed / 26 jr-two-bed / 24 three-bed / 9 studio), 6 storeys, height 22.5m; FSR 2.58; gross lot 9,481.5 m2 (net 6,864.3), coverage 55.5%; parking 364 resident + 41 visitor, 319 bike; 27 adaptable.",
    "DP101434": "25-unit townhouse development (7065 204 Street, Cre8): gross lot 1.0 ac (43,560 sq ft / 4,047 m2), coverage 43.7%; parking 50 + 5 visitor; SR-2 -> CD (25 UPA).",
    "DP101429": "S3 Concrete batch plant + office (27447 56th Avenue, G3): a 1-storey office (2,190 sq ft / 203.46 m2) ancillary to a ready-mix concrete batch plant; site 3.17 ac (12,782 m2); M-3 Heavy Industrial.",
    "DP101431": "Gloucester Industrial (26713 56 Avenue, PLACE / aggregate): warehouse + office; total building area 30,390.66 m2 (319,193.77 ft2: warehouse 28,880.8 + office 736.6 + mezzanine 736.6 + utility 36.52); site 12.84 ac (51,968.41 m2), coverage 57%; height 14.28m (variance); parking 199 (156 industrial + 43 office), 18 bike, 2 loading; -> M-2.",
    "DP101440": "Zenterra Latimer (7600 Block of 198B Street): 4 six-storey apartment buildings, 620 units; CD-178; shared underground parkade; Willoughby/Latimer.",
    "DP101432": "Astria self-storage (19723 88 Avenue): a 6-storey self-storage building with ground-floor industrial units; rezone a 1.24 ha portion to Service Industrial M-1A.",
    "DP101413": "Comfort Inn addition (26574 Gloucester Way, Kenneth Kim): existing hotel + a 24-suite addition + a new hotel (restaurant + 33 suites) + parkade; 57 new suites (81 existing -> 138 total); lot 10,040 m2 (108,070 sq ft), coverage 25.7%; new hotel height 12.70m; parking 149 (95 existing + 54 new); C3.",
    "DP101406": "Wesmont townhouse development (7620 197 Street, Focus): 26 townhouse units, 2 & 3 storeys; gross lot 2.00 ac (87,153 sq ft / 8,097 m2); parking 55 (52 garage + 3 surface) + 6 visitor; SR-2 -> CD.",
    "DP101430": "Aldergrove Industrial Park Phase 1 (26090 30A Avenue, Planworks/aggregate): Building A warehouse 20,468.8 m2 + office 625.9 m2 + mezzanine, total 21,776.5 m2 (234,400 ft2); net site 9.47 ac (38,314.57 m2); height 14.4m; parking 151, 13 bike, 1 loading; 703 Heavy Industrial -> M-3.",
    "DP101415": "Rathod Homes semi-detached development (20125-20169 81A Avenue, Willson): 16 semi-detached dwelling units (lots 13-28, 8 duplex buildings) with accessory detached garages; R-CL(SD); height 9.0m.",
    "DP101402": "Rezoning to CRD-4 (Comprehensive Residential Development, max 63 units/ha / 26.1 UPA, townhouse form), 21588 96 Avenue, Redwoods NP.",
    "DP101391": "Northeast Gordon Estate mixed-use (20615-20738 67/68 Avenue, Langley Properties): ~509 apartments + 99 townhouses + 929 m2 (10,000 ft2) commercial; CD-206 (apartments, 2.02 FSR, 6 storeys) + CD-207 (townhouses, 22 UPA, 3 storeys).",
    "DP101439": "200th & 80th mixed-use tower (Chris Dikeakos / Lorval): 369 residential units + office (89,897 sq ft) + commercial (30,340 sq ft); total 375,560 sq ft (34,891 m2), FAR 4.30; site 87,357 sq ft (8,116 m2); parking 821; a high-rise tower + mid-rise office.",
    "DP101407": "146-unit 4-storey apartment (7135 208 Street, Matthew Cheng): mix 12 one-bed / 95 one-bed+den / 6 two-bed / 16 two-bed+den / 8 three-bed / 9 three-bed+den; GFA 128,326 sq ft (11,921 m2); lot 7,474.30 m2, coverage 44.5%; 79.3 UPA; parking 221, 206 bike, 7 adaptable.",
    "DP101422": "Rezoning to CD-190 for ~46 townhouse units (23 upa), 7860 197 Street, Latimer NP (read from the RZ100759 report).",
    "DP101349": "Rezoning to CD-216 (19942 78B Avenue, Flat): ~79 apartments + 133 hotel rooms + 1,844 m2 (19,852 ft2) ground-floor commercial; Lot 1 hotel (1.5 FSR) + Lot 2 apartment (1.7 FSR); max 6 storeys; Jericho sub-NP.",
    "DP101420": "Rezoning to CD-150 (20370 & 20392 73A Avenue, Flat): ~205 apartments in two buildings; Low Density Apartment 1.85 FSR, up to 5 storeys; Central Gordon Estate NP.",
    "DP101410": "Rezoning to CD-201 for ~18 townhouses, 20792 68 Avenue, Willoughby (read from the RZ100739 report).",
    "DP101404": "Rezoning to CD-190 for ~55 townhouse units (15.4 upa), 19752 80 Avenue, Latimer NP (read from the RZ100770 report).",
    "DP101409": "Subdivision concept (7538 202A Street, McElhanney / Sunsa): 49 lots (12 rowhouse/townhouse + 37 single-family); site 2.02 ha (5.00 ac); Latimer NP.",
    "DP101399": "Rezoning to CD-190 (townhouses, max 74 units/ha / 30 upa, read from the RZ100617 bylaw).",
    "DP101368": "Rezoning + DVP to subdivide into 6 single-family lots (19819 68 Avenue, Coastland), R-CL(B); Routley NP.",
    "DP101354": "20120 86 Avenue mixed-use (Maskeen): a 39-storey high-rise tower + a 6-storey wood-frame residential building, 464 residential units; office + ground-floor retail + daycare + 0.5-acre plaza; gross site 2.34 ac (net 1.73); FSR up to 2.8; Carvolth NP, near the bus exchange.",
    "DP101438": "Development Permit No. 101438 (22652 Fraser Hwy Holdings): a commercial building (Building A) on 22652 Fraser Highway; side lot line setback varied, right-in/right-out access, 8.0m ALR buffer.",
    "DP101408": "Truck Parking Concept (26010 30A Avenue, Pacific Land Group): an industrial truck-parking yard, 51 truck stalls + 43; existing dwelling retained as office/caretaker residence; gross lot 2.01 ha (net 1.71); SR-2 -> M-2 Industrial.",
    "DP101388": "3111 272 Street Mixed-Use Development (David Eaton Architect): a 6-storey mixed-use building, residential over ground-floor commercial.",
    "DP101373": "Report to Council (OCP amendment + rezoning 100245, 161884 Canada / Beedie Group, 5500 Block of 268/272/275 Streets): redesignate 37.20 ha from Golf Course to industrial and rezone RU-1/RU-10 -> CD-185 for ~14.89 ha of industrial land + watercourse conservation + stormwater detention; Gloucester CP.",
    "DP101437": "Triplex Development (20418, 20442 & 20464 76 Avenue, Architecture Panel): 3 triplex blocks (Block 1-3), ~9 units; 76th Avenue Development Ltd; Latimer NP.",
    "DP101433": "Townhouse Development (7734 197 Street, ParaMorph Architecture): a townhouse development (cover sheet only; unit count not shown).",
    "DP101427": "50-unit Townhouse Development (6677 Willowbrooke Connector, Flat Architecture): 50 townhouse units in 18 buildings (project info sheet says 49); Willoughby.",
    "DP101421": "18-lot Subdivision (3170 196 Street & 19642 32 Avenue, RGB Engineering): 18 single-family lots; gross site 9,614 m2; existing SR2 -> R-CL.",
    "DP101418": "Development Permit No. 101418 (Plushwood Pacific Enterprises, 27527 51A Avenue): a building in the Gloucester industrial area (signage per Gloucester DP Guidelines, rooftop mechanical screening, refuse enclosure).",
    "DP101401": "Report to Council (rezoning 100716 + DP 101401 + DVP, 1345725 BC Ltd / McElhanney, 7566 197 Street): rezone 0.37 ha to R-1A/R-CL(B)/R-CL(SD)/R-CL(RH) for 5 rowhouse + 2 semi-detached + 2 single-family lots, with a streamside protection DP; Willoughby/Latimer NP.",
    "DP101400": "Report to Council (OCP amendment + rezoning 100249 + DP 101400, Qualico Developments, 26739 & 26771 24 Avenue): rezone 1.78 ha to R-1A/R-CL(A) for 32 single-family lots + Streamside Protection DP; Aldergrove CP.",
    "DP101395": "Proposed Multi-Family Development (20244-74B & 7452/7434 202A Street, Kasian): 115 apartment units over 6 ground-floor CRUs, 5 storeys; unit mix studio 3 / 1-bed 16 / 1-bed+den 60 / 2-bed 6 / 2-bed+den 19 / 3-bed 11; FSR 1.73 (net), gross site 8,093.97 m2, coverage 49%; parking 193, 30 bike; daycare amenity.",
    "DP101386": "Development Permit No. 101386 (Beedie (29th Street) Holdings, 2883 264 Street): a Streamside Protection and Enhancement DP (SPEA) in the Gloucester industrial area.",
    "DP101383": "Residential Subdivision (20481 73A Avenue, Hub Engineering): 21 single-family lots; existing zoning SR2 -> proposed R-1A.",
    "DP101374": "Development Permit No. 101374 (Bhullar, 20545 76 Avenue): a compact-lot residential development on Lots 11-23 (13 lots), R-1A/R-CL with party-wall and common-element agreements; Willoughby.",
    "DP101372": "Development Permit No. 101372 (1124300 BC Ltd, 20600-20700 Block of 84 Avenue & 8400 Block of 207A Street): a single-family subdivision, Lots 1-4, 9-26 and 51-56 (28 lots), R-1A/R-CL(A)/R-CL(B); Yorkson NP exterior design control.",
    "DP101358": "Proposed Industrial Buildings - Pannu Multi-Tenant (27112 56 Avenue, Architecture Panel / D.Force): 2 multi-tenant industrial buildings (A + B); Gloucester industrial area.",
    "DP101347": "Office/Mixed-Use Flex Employment (19803 & 19837 86 Avenue, David Eaton Architect): an office / flex-employment building with ground-floor retail.",
    "DP101465": "Unison Multi-Family Development (VEN Architecture, 23007): a 6-storey residential apartment building (companion to DP101466); dense project-data sheet, Yorkson/Willoughby (208 St / 82 Ave).",
    "DP101466": "Unison Multi-Family Development (VEN Architecture, 23007): a 6-storey residential apartment building (companion to DP101465); dense project-data sheet, Yorkson/Willoughby (208 St / 82 Ave).",
    "DP101393": "Proposed Subdivision (20418, 20442 & 20464 76 Avenue, GurSimer Design): 36 residential lots at 10 UPA; gross site 14,627 m2 (3.61 ac); mix of R-1A, R-CL(A/B) single-family and R-CL(RH)/R-2/RCL(SD) attached; companion to the triplex DP101437.",
    "DP101389": "Site Plan (21456 80 Avenue, McElhanney, Williams NP): rezone to CD Comprehensive Development for a high-density apartment (2.4 FSR); CD parcel 3,867 m2 + urban park parcel.",
    "DP101435": "Rezoning to CD-190 (1290334 BC Ltd, Bylaw 6101): townhouses, max 74 units/ha (30 UPA), 50% coverage, CD-190 = 2,024 m2 (read from the RZ100690 bylaw).",
    "DP101371": "Rezoning to CD-208 (Qualico Communities, Bylaw 5958): townhouses, 32.5-45 units/ha (13-18 UPA), 40% coverage, CD-208 = 1.0 ha (read from the RO100246 bylaw).",
    "DP101369": "A development permit under the Sunmark Townhomes master rezoning (RO100189/CD-77, 20645-74B Avenue and 7521 & 7547-208 Street): 151 townhouse units + 8 lots (1 SF, 3 rowhouse, 4 semi-detached) across the master site, 10 UPA; Willoughby. Per-folder unit split not separately stated.",
    "DP101365": "A development permit under the Sunmark Townhomes master rezoning (RO100189/CD-77, 20645-74B Avenue and 7521 & 7547-208 Street): 151 townhouse units + 8 lots across the master site, 10 UPA; Willoughby. Per-folder unit split not separately stated.",
    "DP101334": "Chohan 30A Business Centre (26074 30A Avenue, IGEL Arch): two M-2 general-industrial buildings (North A + South B); combined gross floor ~113,778 sq ft (10,570 m2); site 19,148 m2 (4.73 ac), coverage 47.4%; height to 11.54m, 2 storeys; parking 88, 15 bike; RU-1 -> M-2.",
    "DP101331": "76 Avenue & 197 Street townhouses (FLAT Architecture, Harry Aggarwal): 38 compact single-detached units (unit types A-C), 2 storeys; gross site 104,613 sq ft (9,718.8 m2); R-CL(SD); 76 parking + 10 visitor.",
    "DP101317": "80th Avenue South Condos (20722 80th Avenue (southern portion), Station One / Pollyco): 110 residential units over ground-floor commercial, 6 storeys; FSR 2.18 (res 1.88 + comm 0.30); site 4,047.1 m2, coverage 53.6%, 271.6 UPH; height 25.7m; parking 193, 138 bike; CRU 1,214 m2; CD.",
    "DP101314": "Storage facility addition (6296 202 Street, IGEL Arch): a proposed addition to an existing 1-storey mini-storage building; M-2 general industrial, light-industrial main storage; site 4,282 m2, coverage ~61%.",
    "DP101344": "Report to Council (rezoning 100709 + DP 101344, 1345477 BC Ltd / Architecture Panel, 19886-76 Avenue & 7588-198B Street): rezone 0.74 ha to CD-191 for 185 apartment units + 1,975 m2 ground-floor commercial + 300 m2 daycare in two 6-storey buildings, FSR 2.4; Latimer NP High Density Mixed Use.",
    "DP101312": "ATC Gateway (27309 Fraser Highway, PDA / Janda Group): 44 residential units (1-bed 5 / 1-bed+den 5 / 2-bed 34) over ground-floor commercial, up to 6 storeys; FSR 2.28; site 2,387 m2; parking 88, 65 bike; CD (OCP Medium Density Mixed Use, 2 FSR), Aldergrove.",
    "DP101297": "Zenterra Latimer 12 - Lot 3 (7687 200 Street, Focus): 242 units in two 6-storey apartment buildings (Bldg 1 130 + Bldg 2 112). Lot 3 of the overall Zenterra Latimer development (Lots 3 & 4), which the DP101440 staff report states is four 6-storey buildings totalling 620 apartment units.",
    "DP101296": "Alliance Concrete Pumps (26260 30A Avenue, Krahn): a 2-storey general-industrial (F2) building; gross building 3,624 m2 (39,010 sq ft); site 19,225.72 m2 (4.75 ac), coverage 17.59%, FAR 0.19; height 12.0m; parking 47; M-2.",
    "DP101291": "Williams Neighbourhood Commercial Gateway (80 Avenue & 216 Street, ABBARCH / Lorval): a large commercial gateway - Buildings A-L (office, retail, grocery anchor, restaurants) + daycare + a future 5-storey hotel (Building M); total ~29,048 m2 (312,672 sf); site 7.198 ha (17.79 ac); ~1,110 parking; CD-1.",
    "DP101250": "Proposed Multifamily Apartment (20290-72 Avenue & 7160/7185 202B Street, Kasian): 260 units in two 6-storey buildings (Bldg 1 136 + Bldg 2 124); FSR 2.02; gross site 9,829.5 m2 (2.43 ac), coverage 42%; parking 399; SR-2 -> CD. (HTML records 391 units - logged as a conflict for review.)",
    "DP101244": "Hudson Square Phase 1 (19925 & 19957-78B Avenue, Ciccozzi / Essence): 371 residential units in four 6-storey buildings (91+89+87+104) over ground-floor commercial (Bldgs 1&4); FSR 1.75; gross site 17,988.87 m2, coverage 42.9%; parking 644; SR-2 -> High Density Mixed Use. (HTML records 385 units - logged as a conflict.)",
    "DP101242": "103-unit townhouse development (20306 82 Avenue, Cre8): 103 townhouse units in 23 three-storey buildings, 21.82 UPA; gross site 205,436 sq ft (19,085 m2), net 193,274 sq ft, coverage 40.11%; parking 234 (206 in-unit + 28 visitor); SR2 -> CD; Latimer NCP.",
    "DP101270": "Report to Council (OCP amendment + rezoning 100267 + DP 101270, 1342692 BC Ltd / Common Ground, 6815-204 Street): rezone 0.6 ha to CD-212 for 143 apartment units in two 4-storey buildings, FSR 1.62; redesignate Townhouse A -> Medium Density Apartment; Willoughby.",
    "DP101194": "Multi-Family Residential (27257/65 28A Avenue, David Danyluk Architect): a 43-unit multi-family building; RU-1 -> CD; Carvolth.",
    "DP101190": "Mixed-Use Midrise Development (20069 80th Avenue, Ciccozzi / Pollyco): a mixed-use midrise (residential over ground-floor commercial), SR-2 -> CD. Unit count is on the dense project-data sheet (not legible at render scale).",
    "DP101180": "High Point (6868/6842 202B Street & 20309/39/47 68 Avenue, Kasian): 100 townhouse units (9 unit types) in 3-storey buildings; FSR 0.76 (net); gross site 20,358.21 m2 (5.03 ac); height 9m; density 20.5 UPA net; parking 221 (200 garage + 21 visitor); SR-2 -> townhouse CD.",
    "DP101225": "Report to Council (OCP amendment + rezoning 100183 + DP 101105 & 101225, 1078245/1118375 BC Ltd / HY Engineering, 20186 & 20212-86 Avenue): 223 apartment units in two 6-storey buildings, GFA 17,640 m2, FSR 2.62 on the southern 0.67 ha; CD-186; parking reduced 335 -> 326; Carvolth NP.",
    "DP101218": "Glover Mary Church Mixed Use Development (9140 Glover Road & Mary Avenue, Focus): Buildings 1 & 2 with ground-floor commercial and residential above, 47 units total; CD-2; Fort Langley.",
    "DP101162": "66-unit condominium development (20276 72B Avenue, FLAT Architecture): a 4-storey condo, 66 apartment units; SR-2 -> CD; Central Gordon Estate / Willoughby. Companion to DP101161.",
    "DP101161": "66-unit condominium development (20267 72 Avenue, FLAT Architecture): a 4-storey condo, 66 apartment units; SR-2 -> CD; Central Gordon Estate / Willoughby. Companion to DP101162.",
    "DP101157": "Report to Council (OCP amendment + rezoning 100178 + DP 101157, 20148/20152/20180 82 Ave Holdings): 16 rear-loaded semi-detached units in 8 buildings (R-CL(SD), 2-storey) + 68 townhouse units (CD-131, 3-storey, 19 UPA) = 84 units. (HTML records 12 - logged as a conflict.)",
    "DP101143": "Part of the Unison Multi-Family Development (8192 208 Street, VEN Architecture, 23007): a 6-storey residential apartment at 208 St / 82 Ave, Yorkson. Companion to DP101465/DP101466.",
    "DP101114": "Heritage Revitalization Agreement + DP 101114 (Berry General Store / Mounpillar, 23210 Fraser Highway): restore the c.1930 Berry General Store as a bakery/cafe with a residential unit above, plus a new building with ground-floor commercial and two residential units; C-3 Service Commercial; Aldergrove.",
    "DP101105": "Report to Council (rezoning 100183 + DP 101105, 20186 & 20212-86 Avenue, Carvolth): 223 apartment units in two 6-storey buildings, GFA 17,640 m2, FSR 2.62; CD-186; parking 326. Companion to DP101225.",
    "DP101013": "Report to Council (rezoning 100532 + DP 101013, 1077420 BC Ltd / Apna Group, 7042 & 7058-204 Street): rezone to CD-115 for 37 townhouse units in 8 three-storey buildings (2 two-storey), 18.6 UPA; Willoughby.",
    "DP100999": "Mixed Use Development - Cornerstone Estates (7007 204 Street, BHA Architecture): a mixed-use (residential + commercial) development; detailed unit/area statistics are on a dense rotated project-statistics sheet.",
    "DP100962": "Report to Council (OCP amendment + rezoning 100188 + DP 100962, QRD Willoughby Holdings, 7000 Block 202B Street): rezone to CD-148 for 87 townhouse units in 18 three-storey buildings (3-bed), 16.8 UPA; 59 double garage + 27 tandem; Central Gordon Estate.",
    "DP100957": "Proposed Multi-Family Development (20350 & 20326-72 Avenue, DF Architecture): a 6-storey apartment building (201 units per HTML); gross site 1.84 ac; SR-2 -> CD.",
    "DP100911": "Report to Council (rezoning 100493 + DP 100911, 1116005 BC Ltd / Apna Group, 20434-72 Avenue): rezone to CD-154 for 27 townhouse units in 5 buildings (3-storey, 1 two-storey), 15 UPA; parking 61; Willoughby.",
    "DP101302": "Report to Council (rezoning 100604 + DP 101302 & 101226, Royale Properties (208) Ltd, 7581-208 Street): rezone to CD-169 for 75 townhouse units in 13 three-storey buildings (3-bed), 15 UPA (37.5 UPH); 67 double garage + 8 tandem; Yorkson. Companion to DP101226.",
    "DP101226": "Development permit paired with DP101302 under rezoning 100604 (Royale Properties, 7581-208 Street): 75 townhouse units in 13 three-storey buildings, CD-169, 15 UPA; Yorkson.",
    "DP101477": "Under the Astria Properties RZ100764 rezoning (the staff report names the companion DP101432), 19723-88 Avenue: rezone a 1.24 ha portion to M-1A Service Industrial for a 6-storey self-storage building with ground-floor industrial units; Carvolth NP.",
    "DP101524": "Two T460 35'x160' (20-ft bay) fabric storage structures (3265-264th Street, Tentology Co.): industrial fabric buildings on a paved yard.",
    "DP101520": "Pollyco Group Block B tenant improvement (7980-206A Street, KP Architecture / Krahn): 3 ground-floor commercial retail units (CRU 1 185.84 + CRU 2 101.94 + CRU 3 105.38 m2) with P1 storage; commercial CRU TI.",
    "DP101333": "North Arc 76 Ave compact-lot subdivision (19784 & 19808-76 Avenue): 27 compact lots - R-CL(SD) semi-detached (Lots 1-6, 16-26) + R-CL(RH) rowhouse (Lots 7-15) + Lot 27; Latimer NP.",
    "DP101212": "Compass Cohousing (20353-66 Avenue): a 40-unit cohousing (residential) development.",
    "DP101307": "Pollyco Block C (20722-80 Avenue, Yorkson): a mixed-use development (companion block to the 80th Avenue South Condos, DP101317); DP Area J signage, rooftop-mechanical screening.",
    "DP101550": "Nook Langley design-development review (300-20020 Willowbrook Drive, Assembledge+ / MGBA): a commercial tenant-improvement fit-out.",
    "DP101457": "Prefab modular building (Whitley 9'9\" x 50' module) at 26103-30A Avenue, Aldergrove.",
    "DP101538": "DP instrument (Jean & Lies Reimer, 22991-88 Avenue, Fort Langley): a 3-unit residential development; conditions are tree protection + refuse screening.",
    "DP101508": "Neighbourhood Forest Fire Hazard Reduction DP (Jassar, 3650-203A Street, Brookswood-Fernridge): exterior design control registered at subdivision.",
    "DP101259": "Streamside Protection and Enhancement DP (161884 Canada / Beedie, 27522-60 Avenue, Gloucester industrial area). Companion to DP101258.",
    "DP101290": "Streamside Protection and Enhancement DP (Qualico Developments, 7711-206 Street).",
    "DP101125": "Streamside restoration/enhancement DP (Mitchell Latimer 77/197, 19610-78 Avenue & 7661-197 Street); protects a minimum 2,266 m2 of off-site lands.",
    "DP101086": "Streamside Protection and Enhancement DP (0808469 BC Ltd, 20497 & 20543-74B Avenue); 89-unit residential development (per HTML).",
    "DP101145": "Master development permit (Martini / Emvico / Vivico et al, 21422/21427/21476-83 Avenue plus the 21300 Block of 82/83 Avenue and 8000-8300 Blocks of 216 Street) in the Williams NP; a large multi-parcel mixed-use assembly.",
    "DP100822": "Streamside Protection and Enhancement DP (Vesta Properties Latimer Phases 1 & 2, 20144-84 Avenue & 8278-200 Street).",
    "DP101081": "Exterior design-control DP (Devender & Rajvinder Bhullar, 20545-76 Avenue, Yorkson): Lots 1-10 zoned R-1A / R-CL(A) / R-CL(B); same site as DP101374 (Lots 11-23).",
    "DP101490": "Fire-hazard and sensitive-ecosystem protection DP for the 19757-24 Avenue mixed development (companion to DP101491 / RZ100783); classified from the related applications.",
    "DV100167": "Parking variance for the Cornerstone Estates mixed-use development (7007-7033 204 Street; companion DP100999 / RO100215, 177 units).",
    "DV100168": "Parking variance for the Carvolth 223-unit residential development (20186 & 20212-86 Avenue; companion DP101225 / DP101105).",
    "DV100180": "Parking/setback variance for the Apcon 200 St mixed development (8146 200 Street; companion DP101454 / RO100296, 245 units).",
    "DV100182": "Landscape/amenity variance for a mixed development at 21588-96 Avenue (companion DP101402 / RO100256).",
    "DV100184": "Parking variance for the Edgar multi-family development (7883-199 Street; companion DP101471).",
    "DV100196": "Lot-size/width variance for a 5-lot rural residential subdivision at 24108-52 Avenue.",
    "DV100198": "Building-height variance (12.0 -> 12.9m) for the 3365-264 Street industrial building (companion DP101486).",
    "DV100199": "Building-height variance (12.0 -> 12.95m) for the 3350-262 Street industrial building (companion DP101485).",
    "DV100200": "Building-height/parking variance for the 8740 & 8750-204 Street commercial development (companion DP101506).",
    "DV100207": "Siting/parking variance for the Amar fourplex (20996-83A Avenue; companion DP101499, 4 units).",
    "DV100209": "Front-setback variance for a mixed development at 3180-208 Street (companion DP101525 / RZ100800).",
    "DV100210": "Lot-frontage variance for the 18-lot residential subdivision at 3170-196 Street & 19642-32 Avenue (companion DP101421).",
    "DV100212": "Amenity-area variance for the Randhawa mixed-use (apartments + daycare) at 27251-30 Avenue (companion DP101497).",
    "DV100216": "Building-height variance for a mixed development at 2880-260 Street (companion DP101542 / RZ100805).",
    "DV100218": "Landscape-buffer variance for an industrial lot at 27099-8 Avenue (companion RO100312, Gloucester 8 Ave).",
    "DV100219": "Landscape-buffer variance for an industrial lot at 27043-8 Avenue (companion RO100311, Gloucester 8 Ave).",
    "RZ100700": "Heritage Revitalization Agreement for the Berry General Store (23210 Fraser Highway, Aldergrove): heritage commercial bakery/cafe + residential units; C-3 (companion DP101114).",
    "RO100258": "Plan-boundary amendment (remove 22940-48 Avenue from the Rural Community Plan into the Murrayville Community Plan) for an institutional site (companion RZ100797).",
    "SA101371": "Lot-line adjustment at the Gloucester West industrial lands (5670-264 Street / 26477-56 Avenue; companion DP101507 / DP101476 / DP101475).",
    "TU000008": "Temporary Use permit for truck parking at 27351-8 Avenue, RU-2 (industrial; companion RO100330).",
    "DP100909": "Streamside Protection DP in conjunction with a residential subdivision at 3350/3400/3438-205 Street (companion SA100986).",
    "LP000069": "Liquor & Cannabis licence - 4,000-person special-event-area endorsement on an existing manufacturer (winery/brewery) licence at 25160-72 Avenue, RU-3.",
    "LP000072": "Liquor & Cannabis licence - three 400-person special-event-area endorsements on an existing manufacturer licence at 1268-216 Street, RU-3.",
    "RZ100733": "Site-specific RU-5 text amendment to permit a processing/distribution facility for growing media (greenhouse/horticulture/nursery sector) at 25044-88 Avenue; industrial. ALC approved.",
    "SA101367": "Homesite Severance subdivision under ALC Act s.21(2) at 19636-8 Avenue, RU-1; residential homesite. ALC approved.",
    "TT000016": "Proposed 62m telecommunications tower at 8550-252 Street, RU-2; utility/infrastructure (classified institutional).",
    "TT000025": "Proposed 28m telecommunications tower at 21489-58 Avenue, RU-3; utility/infrastructure (classified institutional).",
    "TU000007": "Temporary Use permit to allow vehicle parking at 20054-96 Avenue; industrial outdoor-storage use.",
    "TU000015": "Temporary Use permit to allow truck parking at 23305 Old Yale Road, RU-1; industrial.",
    "AL100405": "ALC non-farm use application (6614 Glover Road, RU-3): a proposed equipment-parking/storage area on the farm (adjacent to a mobile home and future residence); classified industrial (outdoor equipment storage).",
    "AL100472": "ALC non-farm use application (27017-20 Avenue, Aldergrove, RU-2): proposed event/gathering areas on existing lawns with a pavilion and temporary gravel parking; classified commercial (event venue).",
    "AL100475": "ALC non-farm use application (24550-72 Avenue, RU-3): permit/adjust liquor service areas accessory to the Thunderbird Show Park equestrian centre (~34 ha); classified commercial (event/recreation venue).",
    "AL100478": "ALC non-farm use application (22311 No. 10 Highway, RU-3): a private family mausoleum (max 4 crypts) on a 0.2 ha portion; classified institutional (interment use).",
    "RZ100750": "Proposed eight-storey commercial building (8929-201 Street, Northland / Pacific Coast Architecture): ground-floor retail + 7 floors office; FSR 1.6, floor area 8,153 m2; site 4,962 m2; parking 213; CD-48 height amendment.",
    "RZ100765": "Site-specific RU-2 amendment to permit a film 'Production Studio' use (1034-264 Street, 1.85 ha of a 9.9 ha property); commercial/media production. ALC conditionally approved (Res. #363/2023).",
    "SA101406": "Air-space-parcel subdivision (Veritas, 20343/20387-70 Avenue & 20386/20342-70A Avenue, CD-189): a residential development of up to 6-storey apartment buildings (A, B, C, D) over a shared parkade.",
    "SA101408": "Preliminary 2-lot subdivision (6897 Glover Road & 21690 Smith Crescent, CD-167): a 3.94 ha site with an existing sales/admin office and maintenance buildings split into two ~1.97 ha lots; classified commercial (low confidence - use not fully stated).",
    "DP101299": "Zenterra Latimer development (7687 200 Street, Lots 3 & 4): per the DP101440 staff report, four 6-storey apartment buildings totalling 620 units (Lot 3 = 242, per DP101297); shared underground parkade; CD-178. (The earlier rezoning HTML recorded 429.)",
    "RO100224": "Rezoning/OCP for the Zenterra Latimer development (7687 200 Street, Lots 3 & 4): the current DP101440 report states four 6-storey apartment buildings totalling 620 units; CD-178. (The rezoning HTML recorded 429, now superseded by the DP's 620.)",
    "RO100218": "Williams Neighbourhood Commercial Gateway (80 Avenue & 216 Street, ABBARCH / Lorval): a large commercial gateway (Buildings A-L office/retail/grocery/restaurant + daycare + future 5-storey hotel M); total ~29,048 m2 (312,672 sf); site 7.198 ha (17.79 ac); ~1,110 parking; CD-1. Companion to DP101291; address read from the architectural drawings.",
    "RO100279": "Multi-Family Residential Development (F. Adab Architects, 84 Carvolth Holding; 20239 84 Avenue / 8400 Block of 202B Street): 100 apartment units (1-bed 36 / 1-bed+den 16 / 2-bed 26 / 2-bed+den 7 / 3-bed 15), 6 storeys; FSR 2.8, gross floor 86,476 sq ft (8,033 m2); lot 2,870 m2; parking 150 (165 incl visitors); SR-2 -> CD (Carvolth NCP).",
    "RZ100594": "8045 198A Street (1168656 BC Ltd / Krahn Engineering; also DP101130): two 4-storey multi-tenant office buildings - Building A 1,479 m2 (15,929 sq ft) + Building B 1,957 m2 (21,060 sq ft); lot coverage 36%; SR-2 -> CD-157 Business Office Park; Latimer NP. Commercial (office), not mixed.",
    "DP101130": "Development Permit for the 8045 198A Street office park (Krahn Engineering): two 4-storey multi-tenant office buildings (Building A 1,479 m2 + Building B 1,957 m2 = 3,436 m2), lot coverage 36%; CD-157 Business Office Park, DP Area 'H' Business Technology Park; Willoughby. Commercial (office), not mixed.",
    "DV100166": "Development variance at 8045 198A Street for the two-building 4-storey office development (RZ100594 / DP101130, Business Office Park CD-157); commercial (office).",
    "DP101284": "Jericho Crossing - Phase 2 (19976 & 19970 80th Avenue, Ciccozzi / Essence): three buildings, ~347 units (232 market: Bldg 1 123 + Bldg 2 109; Bldg 4 rental 115) over commercial + daycare + office, up to 7 storeys; FSR 1.97; High Density Mixed Use. (HTML's 740 is the full Jericho Crossing across both phases.)",
    "DP101274": "Multi-family mixed-use (22260-22288 48th Avenue, Murrayville, Ankenman Marchand): 75 units (studio 9 / 1-bed 25 / 2-bed 38 / 3-bed 3) over ground-floor commercial + daycare; GFA 79,001 sq ft (7,339 m2), FSR 1.97; site 40,160 sq ft (3,731 m2), coverage 52%, 201 units/ha; height 18.04m; parking 110; SR1 -> CD.",
    "DP101257": "Conwest Mixed-Use (9135 King Street, Fort Langley, Site Lines): a 3-storey mixed-use building - 22 residential units over commercial (restaurant + CRU); GFA 29,329 sq ft (2,724.83 m2), FAR 1.57; lot 18,672 sq ft (1,734.7 m2), coverage 60.2%; parking 51 (13 above + 38 underground); Civic Institutional P1 -> CD. (HTML recorded 26 units.)",
    "DP101124": "Townhouse development (6834/6904 204 Street & 20441 68th Avenue, Willoughby): 41 townhouse units (incl 2 adaptable) in 9 buildings, 3 storeys; FAR 0.84 net; gross lot 79,862 sq ft (1.84 ac), net 59,394 sq ft, coverage 45% gross / 38% net; density 25.31 UPA net; height 12.08m; 82 garages; SR-2 -> CD.",
    "DP101221": "Three six-storey mixed-use buildings (19930/19962/19984 76 Avenue, FLAT Architecture / Ace Point): 302 residential units (Bldg 1 80 + Bldg 2 91 + Bldg 3 131) over underground parkade, daycare, CRU and eatery; FAR 2.84 net, coverage 35%; parking 535; RA -> CD (RF70).",
    "DP101335": "Report (rezoning 100585 + DP 101335, 1273694 BC / Coastland, 7445-198B Street): rezone SR-2 -> R-CL(A)/R-CL(SD) for 8 lots - 4 single-family + 4 semi-detached (in two buildings), 2.5 storeys, 8 UPA; Latimer NP.",
    "DP101305": "8075 198A Street: a development under Comprehensive Development Zone CD-179 (business/industrial park - office, warehouse/wholesale distribution, research & high-tech uses, with accessory group daycare and one restaurant), max 25m / 5 storeys. The rendered document is the CD-179 zoning bylaw text (no project-specific unit/area data on it).",
    "DP101087": "Multifamily residential (6862, 6876 & 6898 204 Street, FLAT Architecture): 62 townhouse units (types A-D) in 12 buildings, 3 storeys; density 20.33 UPA / 50.29 UPH; SR-2 -> CD.",
    "DP101320": "6785 208 Street: a townhouse development under CD-205 (Bylaw 6044) - townhouses / live-work townhouses, max 54 units/ha (22 UPA), 45% coverage, 3 storeys. (HTML records 78 units.)",
    "DP101179": "Report (rezoning 100593 + DP 101179, 1185954 BC / Kumar Architecture, 7189-208A Street): rezone SR-2 -> R-CL(RH) to subdivide into 5 rowhouse lots, 2 storeys + basement, 39% coverage; parking 11 (10 + 1 visitor); Willoughby. CAC $5,776/unit x 5.",
    "RO100320": "Park Ridge Townhouse Development (20448/20460/20492 74B Avenue, Keystone): 79 townhouse units (Type A 34 / B 41 / C adaptable 4) in 3-storey buildings; gross site 158,468 sq ft (14,722 m2, 3.64 ac), net 132,347 m2... FAR 0.85, coverage 34% gross / 41% net; height 12m; parking 174 (158 resident + 16 visitor); SR-2 -> CD, 64 UPHA; Willoughby.",
    "RO100316": "6-storey Mixed-Use Development (19963 82 Avenue, F. Adab): 152 units (studio 10 / 1-bed 98 / 2-bed 27 / 3-bed 17) over ground-floor commercial; FAR 2.74; lot 4,057.2 m2 (1.0 ac), coverage 55%; residential GFA 103,261 sq ft + commercial 4,552 sq ft; parking 231 (188 apt + 20 visitor + 21 CRU), 152 bike; SR-2 -> CD.",
    "DP101076": "Multi-family residential (20145 & 20185 84 Avenue, FLAT Architecture): a 127-unit 6-storey apartment + 42 stacked-townhouse units = 169 units; parking 330; SR-2 -> CD; Carvolth.",
    "RO100332": "Townhouse development - Lot A (7261/7317 209A Street & 20891 72 Avenue, Focus / Smith JV): 83 townhouse units (mostly 3- and 4-bed) in 17 three-storey buildings; net lot 149,030 sq ft (13,845 m2, 3.42 ac), density 24.3 UPA, coverage 24.4%; parking 185 (167 resident + 18 visitor); SR-2 -> CD.",
    "DP101096": "Comprehensive Development apartment (7310 202B Street, Matthew Cheng Architect): 94 units in a 6-storey building (1-bed 13 / 1-bed+den 37 / 2-bed 25 / 2-bed+den 8 / 3-bed 4 / 3-bed+den 7); FSR 2.0, GFA 8,114.24 m2; site 4,046.4 m2 (0.99 ac); parking 142; SR-2 -> CD. (The related RO100180 rezoning recorded 108; HTML has 7130 202B St.)",
    "DV100185": "UniFirst industrial-laundry building addition (9189 196A Street, SK Architect): a plant + office addition (361 m2) to the existing M-2 industrial laundry; total GFA 3,694 m2, FSR 0.404; lot 10,036 m2 (2.48 ac), coverage 36.22%; height 7.82m, 1 storey; parking 36; variance to the front setback.",
    "RO100318": "Townhouse development (19820 69 Avenue, FLAT Architecture): 22 three-bedroom townhouse units in 5 buildings + age-friendly amenity, 3 storeys; gross site 45,203 sq ft (4,199.49 m2, 1.04 ac), net 39,692 sq ft; density 24 UPA net, FAR 0.83 net, coverage 27.22%; parking 67 (62.8 resident + 5 visitor); SR-2 -> CD; Routley South.",
    "RZ100553": "Mixed-use development (20200 Block of 86 Avenue / 202B Street, Isle of Mann): 203 residential units (1 studio + 202 apartments) in 5- and 6-storey buildings over 354 m2 commercial; density 2.7 and 2.1 FSR; parking 318 (308 apt + 10 commercial); CD-171; Carvolth.",
    "RZ100778": "Mixed-Use Mid-Rise (19942/19960/19986 83 Avenue, Ciccozzi): 297 residential units (1-bed 121 / 2-bed 140 / 3-bed 36) in three 6-storey buildings over 3,710 m2 commercial retail; FSR 2.97 net; gross site 10,813.39 m2 (2.67 ac); parking 580 (427 standard + 153 visitor), 306 bike; SR-2 -> High Density Development Zone.",
    "RO100310": "Townhouse development (8080 & 8126 197 Street, FLAT Architecture): 71 three-bedroom townhouse units (Type A 52 / B 15 / C 4) in 12 buildings + indoor & age-friendly amenity, 3 storeys; gross site 174,295 sq ft (16,192.53 m2, 4.01 ac), net developable 108,622 sq ft; density 22 UPA, FAR 0.68, coverage 27.36%; parking 156 (142 resident + 14 visitor); SR-2 -> CD; Latimer NW.",
    "RO100331": "Hatchcreek Estates feasibility subdivision (23045 48 Avenue, Aplin & Martin): rezone 2.17 ha (net) from Rural to SR-3 for 9 single-family lots (existing 1 lot -> 9); density 4.15 uph net; Hatchery Creek (Class A) + ALR buffer.",
    "RO100158": "Pollyco Latimer Ventures (7484/7536/7570 200 Street & 7441/7495/7541 202A Street; DPs 100967/101079/101097): includes 42 semi-detached units in 21 buildings (R-CL(SD), 2-2.5 storeys, 44% coverage) plus a rowhouse/townhouse component; parking 139; Latimer NP. (The 42 is the semi-detached portion of the larger multi-DP rezoning.)",
    "RO100177": "Carvolth 86th Ave LP (20148 & 20176 86 Avenue): a Work/Live Flex mixed-use building (Building A at 2.34 FSR) + Medium/High Density Residential; overall gross floor area 37,573 m2 (404,449 sq ft); Carvolth NP uses FSR (not a unit cap). HTML records 371 units.",
    "RZ100713": "Bakerview subdivision (20156 & 20160 28th Avenue): rezone SR-2 -> R-1B & R-1C for 8 single-family lots (~544-553 m2 each).",
    "RZ100777": "Jim Pattison Developments (3228 & 3258 200 Street): rezone to CD-184 for approximately 452 apartments + 12,476.88 m2 (134,300 sq ft) of commercial/office, up to 2.3 FSR and 6 storeys; Willoughby. Conceptual - unit count may change at DP.",
    "DP100868": "Oasis / Care Pacific Maple Gardens (7743 & 7787 200 Street): a phased seniors/continuum-of-care development, CD-118 (Jericho SNP), 7 buildings 4-20 storeys at FSR 1.25. Phase 1 is a 20-storey seniors' building of 196 units (licensed care + seniors + rental/condo apartments) by Element Lifestyle (Oasis).",
    "DP100900": "3134 200 Street: a townhouse development under Comprehensive Development Zone CD-116 (townhouses, max 44 units/ha, 40% coverage, 3 storeys). HTML records 82 units.",
    "DP100940": "A development permit under the Williams NP Phase 1 (21213 80 Avenue area, Qualico / RO100227): rowhouse / townhouse-strata development; see the companion DP101214. Parking-rationale memo read (no separate per-folder unit table).",
    "DP101012": "Report (rezoning 100203 + DP 101012, 1077420 BC / Apna Group, 20443 70 Avenue & 7010 204 Street): rezone SR-2 -> CD-151 for 135 condominium units (Bldg A 48 + Bldg B 40 four-storey apartments; Bldg C five-storey mixed-use with 47 condos) + 1,028 m2 commercial (7 CRU); 60 UPA; Central Gordon Estate.",
    "DP101031": "Grayrose Heritage Alteration (21600 Block of 48 Avenue, Murrayville): rezone SR-1 -> CD-153 for two buildings with ground-floor commercial (retail/cafe/office/wine store) and up to 2 residential units on the second storey.",
    "DP101166": "4-storey multi-tenant office building (19781 80 Avenue, Zenterra): gross floor area 4,304 m2 (46,331 sq ft), ~24 office-use units; parking 170 (2-level underground + 16 surface); SR-2 -> CD-157 Business Office Park; Willoughby. Commercial (office).",
    "DP101213": "Qualico Mitchell Williams (21247-21397 78 Avenue & 21264-21392 80 Avenue): Williams NP rezoning creating 7 front-loaded single-family lots (R-1F) + 2 future townhouse lots (CD-172), with an elementary school site. HTML records 327 units across the Williams development.",
    "DP101214": "A development permit under the Williams NP Phase 1 (21252 83 Avenue area, Qualico / RO100227): rowhouse / townhouse-strata development; companion to DP100940.",
    "DP101287": "7100 206 Street: a townhouse development under CD-215 (townhouses / accessory uses, max 54 units/ha = 22 UPA, 50% coverage, 3 storeys). HTML records 83 units.",
    "DP101310": "Langley88 Spec Building (8870 196A Street, Architecture Panel / D.Force): an industrial warehouse + 2-storey offices; footprint 56,785 sq ft (5,275 m2), total 5,528 m2; net lot 11,786 m2, coverage 44.8%; height 11.18m; parking 42; SR-2 -> CD. Industrial.",
    "RO100265": "Willowtree Towers (19653 & 19665 Willowbrook Drive, Ciccozzi): a mixed-use residential development - 1,586 units (1-bed 840 / 2-bed 572 / 3-bed 174) in four towers (44 & 40 storeys) over a 6-storey podium; FSR 7.54; residential GFA 126,750 m2 + 4,228 m2 commercial; parking 2,500; Commercial -> CD.",
    "RO100272": "Willowbrook Towers (19677 Willowbrook Drive, Ciccozzi): a mixed-use residential development - 1,065 units (1-bed 637 / 2-bed 394 / 3-bed 34) in three towers (40/34/28 storeys) over a 6-storey podium; FSR 7.44; residential GFA 81,827 m2 + 1,411 m2 commercial; parking 1,638; Commercial -> CD.",
    "RO100292": "KKS Nations mixed-use masterplan (Lot 27, between 201 Street & 86 Avenue, Arcadis): ~600 units of mixed-tenure housing in two buildings / four 6-12 storey towers over connected podiums, + ~1,050 m2 commercial retail + 415 m2 community amenity (KKS cultural centre); ~550 parking; combined density 2.87 FSR; two phases.",
    "RO100323": "37 Townhouse Units (8071 197 Street, Cre8): 37 townhouse units in 7 buildings, 3 storeys; gross site 86,966 sq ft (1.99 ac), coverage 26.56%; density 17.58 UPA; parking 83 (74 in-unit + 9 visitor); SR-2 -> CD; Latimer NCP.",
    "RO100327": "200th Street Multi-Family (2759 200th Street, KP Architecture / Krahn): a split-use site - east portion rowhouse/townhouse (R-CL(RH2)/CD, max 20 UPA) and west portion employment lands (M-1A/M1-B); Booth NP. HTML records 36 units (townhouse portion).",
    "RZ100787": "Ivan Wang Residential subdivision (20731 72 Avenue, Aplin & Martin): subdivide into 3 lots - CD Low Density Mixed Use (1.4 FSR, ~45 units) + CD High Density Apartment (2.4 FSR, ~140 units) + RM-3 Townhouse (~33 units) = ~218 units; Smith NP.",
    "RZ100790": "Rezoning (7410/7438 208 Street & 7423/7428/7400 Block 209A Street): rezone SR-2 -> CD-220 (apartment, up to 2.4 FSR, 6 storeys) for the westerly lot + CD-131 (townhouse, up to 22 UPA, 3 storeys) for the easterly lot; subdivide into 2 lots; Smith NP.",
    "DP100896": "Report (rezoning 100478 + DP 100896, 1135881 BC / Kasian, 3321 200 Street & 19882/19944 33A Avenue): rezone to C-2 + CD-182 for 37 townhouses + 60 low-rise apartment units + 3,157 m2 (33,982 sq ft) commercial; Brookswood-Fernridge.",
    "DP100930": "Report (OCP amendment + rezoning 100254, Qualico Developments Aldergrove, 2600 Block of 268 Street): rezone to CD-115/R-1B/R-CL(A) for 25 single-family lots + 45 townhouse units (net density 12.5 UPA); Aldergrove CP.",
    "RO100142": "Leavitt Holdings (24389 Fraser Highway & 3939 244 Street): rezone to CD-116 for office + industrial buildings (concrete tilt-up), 3 storeys, 13% coverage; parking 119 (98 office + 21 industrial). Industrial/office.",
    "RZ100708": "Sandhill Brookswood Townhouse (20800 40th Avenue, Site Lines): 16 townhouse units in 5 blocks + tandem units, 3 storeys; gross site 40,463 sq ft, net 30,596 sq ft (2,842.46 m2, 0.70 ac); FSR 0.86, coverage 39%, 22.78 UPA; parking 35 (36 required, cash-in-lieu for 1), 16 EV; SR-1. Address read from the related site plan (the folder's own page had none).",
    "DP101339": "Development permit for the Sandhill Brookswood 16-townhouse development (20800 40th Avenue; companion to RZ100708 / DV100146); 3 storeys, FSR 0.86, 35 parking; SR-1. Address read from the related RZ100708 site plan.",
    "DV100146": "Development variance for the Sandhill Brookswood 16-townhouse development (20800 40th Avenue; companion to RZ100708 / DP101339). Address read from the related RZ100708 site plan.",
    "DP101269": "Development permit under the Williams NP Phase 1 rowhouse/townhouse development (21213-80 Avenue; companion RO100238). Address read from the related RO100238 site plan (the folder's own page had none).",
    "RO100325": "Rezoning for the Wesgroup proposed food store (grocery) at 8500 200th Street (companion DP101531); the rezoning drawing lists the civic address as 'T.B.D. 200 St', so the assigned address is taken from the DP101531 grocery-store project.",
    "DP101048": "Development permit for the Isle of Mann mixed-use development at 20200 Block of 86 Avenue / 202B Street (companion RZ100553, 203 units, CD-171). Address read from the related RZ100553 report.",
    "SA101255": "Subdivision under the Williams Neighbourhood Commercial Gateway (80 Avenue & 216 Street; companion RO100218 / DP101291). Address read from the related RO100218 drawings.",
}


# Deterministic HTML occasionally MIS-classifies a folder - e.g. a Business Office Park whose
# Purpose prose mentions nearby residential gets parsed as "mixed". apply_claude_pdf never
# overwrites a deterministic class, so a Claude-read correction would only be logged as a conflict.
# These are confirmed corrections (verified against the staff report / DP drawings) and are forced
# after the merge. Keep this list tiny and only for genuine mis-parses.
CLASS_OVERRIDE = {
    # Each was flagged as an HTML-vs-Claude-read development_class conflict (the deterministic HTML
    # parser over- or under-called the use - usually over-calling "mixed" on a stray keyword). The
    # class here is the value read from the staff report / project-data sheet and verified. Occupancy
    # is forced to match. (A sweep of row['conflicts'] for field=="development_class" lists these.)
    "DP101114": ("mixed", ["commercial", "residential"]),  # Berry General Store: cafe + residences
    "DP101130": ("commercial", ["commercial"]),  # 8045 198A St office park
    "DP101166": ("commercial", ["commercial"]),  # 19781 80 Ave office
    "DP101212": ("residential", ["residential"]),  # Compass Cohousing
    "DP101291": ("commercial", ["commercial", "institutional"]),  # Williams Commercial Gateway
    "DP101305": ("industrial", ["industrial", "commercial"]),  # CD-179 business park
    "DP101310": ("industrial", ["industrial"]),  # Langley88 warehouse
    "DP101347": ("commercial", ["commercial"]),  # office / flex employment
    "DP101354": (
        "mixed",
        ["residential", "commercial", "institutional"],
    ),  # tower + office + retail
    "DP101375": ("residential", ["residential"]),  # Latimer Grove townhomes
    "DP101385": ("institutional", ["institutional"]),  # Murrayville Academy gym
    "DP101395": ("mixed", ["residential", "commercial"]),  # 115 apt over 6 CRU
    "DP101402": ("residential", ["residential"]),  # CRD-4 townhouse
    "DP101408": ("industrial", ["industrial"]),  # truck-parking yard
    "DP101426": ("industrial", ["industrial"]),  # cold-storage warehouse
    "DP101430": ("industrial", ["industrial"]),  # Aldergrove Industrial Park
    "DP101432": ("industrial", ["industrial"]),  # Astria self-storage
    "DP101438": ("commercial", ["commercial"]),  # commercial building (HTML wrongly industrial)
    "DP101454": ("residential", ["residential"]),  # Apcon 245-unit apartment
    "DP101477": ("industrial", ["industrial"]),  # Astria self-storage
    "DP101495": ("industrial", ["industrial"]),  # truck-parking yard (HTML wrongly residential)
    "DP101509": ("mixed", ["commercial", "institutional"]),  # commercial + daycare
    "DP101513": ("commercial", ["commercial"]),  # C-2 retail/office
    "DP101520": ("commercial", ["commercial"]),  # CRU tenant improvement
    "DP101525": ("institutional", ["institutional"]),  # Langley Temple
    "DP101531": ("commercial", ["commercial"]),  # grocery store
    "DP101535": ("residential", ["residential"]),  # 6-storey apartment
    "DP101542": ("industrial", ["industrial"]),  # Aldergrove Industrial Park South warehouse
    "DV100166": ("commercial", ["commercial"]),  # 8045 198A St office variance
    "RO100218": ("commercial", ["commercial", "institutional"]),  # Williams Commercial Gateway
    "RZ100594": ("commercial", ["commercial"]),  # 8045 198A St office rezoning
}


def main() -> None:
    """Apply the Claude-read fields to data/processed/langley.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "langley.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    applied = 0
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        filled = langley.apply_claude_pdf(row, fields, document)
        note = NOTES.get(permit_id)
        # Append the context note once; guard so re-running the script never duplicates it.
        if note and note not in (row.get("raw_text") or ""):
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        applied += 1
        print(
            f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
            f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}"
        )
    for permit_id, (cls, occ) in CLASS_OVERRIDE.items():
        row = by_id.get(permit_id)
        if row:
            row["development_class"] = cls
            row["occupancy_types"] = list(occ)
            # The HTML-vs-read class disagreement is now resolved in favour of the read, so drop the
            # stale development_class/occupancy_types conflict entries (keep any other-field conflicts).
            if row.get("conflicts"):
                row["conflicts"] = [
                    c
                    for c in row["conflicts"]
                    if c.get("field") not in ("development_class", "occupancy_types")
                ]
            print(f"{permit_id}: class forced -> {cls} (deterministic HTML mis-parse corrected)")
    # Consolidate AFTER the fields are applied: filling a blank address here lets a row that the
    # harvest-time consolidation could not group (no civic address) now fold into its project. Runs
    # after every apply and is idempotent (merge_related de-duplicates related_permits).
    folded = consolidate.consolidate_by_address(rows)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\napplied to {applied} rows ({folded} folded by address); wrote {path}")


if __name__ == "__main__":
    main()
