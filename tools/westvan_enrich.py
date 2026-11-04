"""Apply the in-session Claude-read PDF fields to the three West Van example rows.

Run after `python -m bc_dev_permits.dataset --municipality west_van --out json`. The fields
below were extracted by Claude reading each application's priority PDF in-session (Deer Ridge
"Plans"; Ottawa "Staff Report"; Bellevue "Architectural Drawings"), per the Claude-read workflow
in the pdf-parsing memory. apply_claude_pdf reuses the shared merge/conflict/provenance code.
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import west_van as wv

# permit_id -> (claude-read fields, source document) from reading each priority PDF.
CLAUDE_READ = {
    # The Creek (preliminary): 3033 & 3055 Marine Drive rezoning RS2 -> CD, a 6-unit rowhouse
    # project in three 2-storey blocks (A/B/C). Architect's PROJECT DATA sheet (A-0.1).
    "3033-3055-marine-drive-townhouses": (
        # No unit count is set from the titleblock: its "Grand total: 6" is 6 floor-area line items
        # (3 blocks x 2 floors), not dwelling units, so the HTML's unit count is left to stand.
        {"development_class": "residential", "occupancy_types": ["residential"],
         "number_of_stories": 2,
         "floor_area": 2281.3, "floor_area_unit": "m2", "floor_area_ratio": 0.33,
         "height_m": 7.62, "lot_area": 6809.3, "lot_area_unit": "m2",
         "parking_vehicle_stalls": 27, "parking_bike_stalls": 22},
        {"title": "Architectural Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/3033-3055-marine-drive_architectural-plans-for-posting-on-web-page-for-preliminary-pim.pdf"},
    ),
    # Amber Rise Apartments, 2130 & 2140 Union Court (preliminary): a 7-storey, 30-unit apartment.
    # Architect's "Floor Area & Parking" sheet (A021).
    "amber-rise-union-court": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "units_total": 30, "number_of_stories": 7,
         "unit_mix": {"1-bed": 2, "2-bed": 11, "3-bed": 10, "townhouse": 7},
         "floor_area": 4183.1, "floor_area_unit": "m2", "floor_area_ratio": 1.04,
         "lot_area": 5264.4, "lot_area_unit": "m2",
         "parking_vehicle_stalls": 51, "parking_bike_stalls": 51},
        {"title": "Proposed Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/amber-rise-union-court-proposed-plans-lots-11-and-12.pdf"},
    ),
    # 660 Duchess Avenue (approved): a duplex (RD3). Architect's PROJECT DATA sheet (A0.01).
    "660-duchess-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "units_total": 2, "number_of_stories": 2,
         "floor_area": 579.24, "floor_area_unit": "m2", "floor_area_ratio": 0.57,
         "height_m": 7.62, "lot_area": 1014.62, "lot_area_unit": "m2"},
        {"title": "Council Report with appendices",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Council%20Report%20-%20Preliminary%20Development%20Proposal%20for%20660%20Duchess%20Avenue%20with%20Appendices.pdf"},
    ),
    # The Cottages at Gleneagles, 6035 & 6075 Marine Drive (preliminary): 17 detached 2-storey
    # single-family cottages (~1,400 sf, 2-3 bed each; 6 offered rent-to-own). Development brief.
    "6035-6075-marine-drive": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "units_total": 17, "number_of_stories": 2},
        {"title": "Preliminary Plans Part 1",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Preliminary%20Plans%20Part%201.pdf"},
    ),
    # Plans sheet P-7 shows a 7-level building (Level Seven = penthouse); the rooftop sauna is an
    # accessory structure (height variance 18.2 -> 19.6 m already captured from the HTML).
    "11-3085-deer-ridge-close": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "number_of_stories": 7},
        {"title": "Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/11-3085-deer-ridge-close-plans-for-webpage.pdf"},
    ),
    # Staff Report: RS3 site of 1,414.9 m2 subdivided into two ~706 m2 lots, a single-family
    # dwelling + secondary suite on each lot.
    "1337-ottawa-avenue-rezoning": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "units_total": 2, "unit_mix": {"detached": 2, "secondary-suite": 2},
         "lot_area": 1414.9, "lot_area_unit": "m2", "zoning_density": "RS3 -> reduced site area"},
        {"title": "Staff Report",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/26may11-16.3.pdf"},
    ),
    # Architectural Drawings (RZ101 site plan): three-storey commercial building at #1507 with two
    # parkade levels below; building footprint 899.8 m2 (9,685 sq ft).
    "1507-bellevue-avenue": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "number_of_stories": 3, "footprint_area": 899.8},
        {"title": "Architectural Drawings",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/ARCH%20Dwg-250503-1507%20Bellevue%20-%20REissued%20for%20Rezoning%20%28ARCH%29%20sealed.pdf"},
    ),
    # --- remaining flagged applications (priority PDF read in-session) ---
    # 221 Maple Lane: Proposed Plans - minor addition to the primary dwelling (powder room), a
    # 33.13 sq ft floor-area variance; single-family.
    "221-maple-lane-edp-variance": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1},
        {"title": "Proposed Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/221%20Maple%20Lane%20-%20Proposed%20Plans.pdf"},
    ),
    # 2213 Lawson Avenue: subdivision survey - Lot 16 (RS5) split into two ~529 m2 lots.
    "2213-lawson-avenue-minor-development-variance-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "lot_area": 1057.6, "lot_area_unit": "m2", "zoning_density": "RS5"},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/2213-lawson-ave-survey-topo-wv6123-signed.pdf"},
    ),
    # 2340 Palmerston Avenue: Difficult-Terrain survey - RS5 site ~1,085.3 m2 subdivided into two
    # ~540 m2 lots; existing 1-storey house, two-storey building envelope.
    "2340-palmerston-avenue-difficult-terrains-development": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "number_of_stories": 2, "lot_area": 1085.3, "lot_area_unit": "m2", "zoning_density": "RS5"},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/2340-palmerston-ave-plans-updated.pdf"},
    ),
    # EDP24-099, 2512 Haywood Avenue (Coleridge Residence): full Project Data & Zoning Summary -
    # RS-4 single-family, lot 735.5 m2, gross floor area 310.77 m2 (FAR 259.28 m2 / ~0.35,
    # relaxation), height 7.39 m, 2 storeys + basement, 1 dwelling / 1 parking space.
    "EDP24-099": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "number_of_stories": 2, "height_m": 7.39, "floor_area": 310.77, "floor_area_unit": "m2",
         "lot_area": 735.5, "lot_area_unit": "m2", "zoning_density": "FAR 0.35",
         "parking_vehicle_stalls": 1},
        {"title": "Proposed architecture plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/2512-haywood-avenue_architectural-plans-july-27-2026_for-neighbourhood-notification.pdf"},
    ),
    # DP22-011, 164 & 172 Oxley Street South (Burrard Inlet Residence): RS4 single-family, lot
    # 1,388.8 m2, floor area 493.5 m2 (FAR ~0.355), height 8.69 m, 25.6% site coverage, 1 parking.
    "DP22-011": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "floor_area": 493.5, "floor_area_unit": "m2", "floor_area_ratio": 0.355,
         "height_m": 8.69, "lot_area": 1388.8, "lot_area_unit": "m2", "zoning_density": "RS4",
         "parking_vehicle_stalls": 1},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/164%20-172%20Oxley%20St%20South%20Approved%20Plans%20-%20Dec%202024.pdf"},
    ),
    # 1456 Gordon Avenue (approved): RS5, an existing dwelling plus a new detached secondary suite /
    # coach house (2 units); combined floor area 304.4 m2, lot 590.1 m2.
    "1456-gordon-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "secondary-suite": 1},
         "floor_area": 304.4, "floor_area_unit": "m2",
         "lot_area": 590.1, "lot_area_unit": "m2", "zoning_density": "RS5"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1456-gordon-avenue-approved-plans.pdf"},
    ),
    # 2859 Bellevue Avenue (Men/Peever Residence, approved): RS4 new construction - a principal house
    # plus a coach house (2 dwelling units); floor area 429.43 m2 (FAR 0.28), lot 1,555.37 m2,
    # 20.3% site coverage, 2 storeys + basement, 3 off-street parking.
    "2859-bellevue-avenue-house-coach-house": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "coach-house": 1}, "number_of_stories": 2,
         "floor_area": 429.43, "floor_area_unit": "m2", "floor_area_ratio": 0.28,
         "lot_area": 1555.37, "lot_area_unit": "m2", "zoning_density": "RS4",
         "parking_vehicle_stalls": 3},
        {"title": "Approved Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1%20Proposal.pdf"},
    ),
    # 3963 Uplands Way: landscape plan for the Hawksley strata amenity area (DP amendment); no new
    # building program, so only the residential (strata) class is added.
    "3963-uplands-way": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": "Proposed plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/3963-uplands-way-proposed-plans.pdf"},
    ),
    # 6707 Nelson Avenue: a 2-car private garage within the Sewell's Landing residential parkade.
    "6707-nelson-avenue-minor-development-variance-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "parking_vehicle_stalls": 2},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/260408-sewells-p4-private-garage-1.pdf"},
    ),
    # 8645 Lawrence Way: RS2 single-family, site 2,847.63 m2, total floor area 5,897.76 sq ft
    # (~547.9 m2), three levels (lower/main/upper), FAR 0.35 max.
    "8645-lawrence-way": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "number_of_stories": 3, "floor_area": 547.9, "floor_area_unit": "m2",
         "lot_area": 2847.63, "lot_area_unit": "m2", "zoning_density": "FAR 0.35"},
        {"title": "Proposed Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/23-127%20-%208645%20Lawrence%20Way%20-%20Proposed%20Plans.pdf"},
    ),
    # DP16-055 Rodgers Creek Lane: "Executive Homes at Rodgers Creek Lane", Area 3 East, Lots 21-41
    # - a 21-home single-family development (this amendment redesigns Lots 22-28).
    "DP16-055": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 21,
         "unit_mix": {"detached": 21}},
        {"title": "Architectural Drawings - Part 1 (original proposal)",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Architectural%20Drawings_Part1.pdf"},
    ),
    # 1768 & 1770 Argyle Avenue (Navvy Jack House, DP24-025): CU5 Ambleside Waterfront zone - heritage
    # restoration of the historic house with a 786 sq ft restaurant/bistro addition; total proposed
    # floor area 2,857 sq ft (265 m2), 2 storeys. A commercial (restaurant) heritage project.
    "1768-1770-argyle-avenue-navvy-jack-house": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "number_of_stories": 2, "floor_area": 265.0, "floor_area_unit": "m2",
         "zoning_density": "CU5 Ambleside Waterfront"},
        {"title": "Approved Architectural Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/SCHEDULE%20A%20to%20DP%2024-025-%20Architectural%20Drawing%20Package%20prepared%20by%20Architectural%20Collective%20dated%20December%205_%202024.pdf"},
    ),
    # 2509 Mathers Avenue (EDP variances): RS4, new single-family home (existing to be demolished);
    # site 796.9 m2, proposed floor area 247.90 m2 (FAR ~0.31), height 7.77 m, 2 storeys + basement,
    # 1 off-street parking.
    "2509-mathers-avenue-edp-variances": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "number_of_stories": 2, "floor_area": 247.90, "floor_area_unit": "m2",
         "floor_area_ratio": 0.31, "lot_area": 796.9, "lot_area_unit": "m2", "height_m": 7.77,
         "zoning_density": "RS4", "parking_vehicle_stalls": 1},
        {"title": "Approved Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/2509%20Mathers%20Avenue%20Approved%20Plans.pdf"},
    ),
    # 1735 Inglewood Avenue (WVFC Soccer Bubble): a seasonal air-supported sports dome over the soccer
    # field (Group A assembly, arena type); 1 storey, gross floor area 847.8 m2 (9,126 sq ft).
    "1735-inglewood-avenue-sports-bubble": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 1, "floor_area": 847.8, "floor_area_unit": "m2"},
        {"title": "Council Report",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/24oct21-7.5.pdf"},
    ),
    # 2330 Cypress Bowl Lane (Mulgrave School STEM Wing): a 4-storey wing attached to the existing
    # school (science/technology programs + a professional-development/conference facility) plus a
    # permanent parking area; combined site 6.984 ha, zoning PA-1. Institutional (school).
    "2330-cypress-bowl-lane": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 4, "lot_area": 69840.0, "lot_area_unit": "m2",
         "zoning_density": "PA-1"},
        {"title": "Council report | July 20, 2026",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/26jul20-12.5_reduced.pdf"},
    ),
    # --- corrections from the user's review (project data read from report / data sheets) ---
    # 2810/2814/2818 Bellevue: foreshore rezoning of three waterfront residences; the meeting
    # notice lists each house's floor area (228.2 / 283.7 / 399.8 m2 -> 911.7 m2 total).
    "2810-2814-2818-bellevue-avenue": (
        {"units_total": 3, "floor_area": 911.7, "floor_area_unit": "m2"},
        {"title": "Meeting notice and information flyer",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/2810-2814-and-2818-bellevue-avenue-notice-and-information-package.pdf"},
    ),
    # 3181/3389 Chippendale & 3861/3875 Uplands Way (Beedie): a four-building strata rezoning - 20
    # storey 63 units, 25 storey 93 units, 16 storey 81 units, 20 storey 87 units (324 units,
    # ~505,000 sq ft -> ~46,900 m2). The per-building list is in the page's prose.
    "3181-3389-chippendale-rd-3861-3875-uplands-way-beedie": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 324,
         "number_of_stories": 25, "floor_area": 46914.0, "floor_area_unit": "m2",
         "rental_or_strata": "strata"},
        None,
    ),
    # 376 Moyne Drive: RS3, lot 1,887.5 m2, 2 dwelling units (primary + the proposed detached
    # secondary suite / coach house of 290.91 m2), FAR 0.26, 2 parking.
    "376-moyne-drive-coach-house-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "secondary-suite": 1}, "floor_area": 290.91,
         "floor_area_unit": "m2", "lot_area": 1887.5, "lot_area_unit": "m2",
         "zoning_density": "FAR 0.26", "parking_vehicle_stalls": 2, "height_m": 4.56},
        {"title": "Proposed Architectural Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/376%20Moyne%20Dr%20-%20Proposed%20Architectural%20Drawings.pdf"},
    ),
    # 875-885 Jefferson Avenue: Report to Council - rezoning RS3 -> RS5 and subdivision of the two
    # lots (2,240.3 m2 total) into four single-family lots, ~200-204 m2 allowable floor area each.
    "875-885-jefferson-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 4,
         "unit_mix": {"detached": 4}, "lot_area": 2240.3, "lot_area_unit": "m2",
         "floor_area": 810.2, "floor_area_unit": "m2", "zoning_density": "RS3 -> RS5"},
        {"title": "Report to Council: June 26, 2023", "url": "https://westvancouver.ca/media/2477"},
    ),
    # 1248 Keith Road: a new duplex (2 units), lot 592.8 m2.
    "1248-keith-road-duplex-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "lot_area": 592.8, "lot_area_unit": "m2"},
        {"title": "Proposed architectural plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1248-keith-road_dp-revision-092526-architectural-plans.pdf"},
    ),
    # 2833 Chippendale Road: a 12-unit townhouse development (HTML), total floor area ~1,755 m2
    # (FAR 0.6); all but two units are 3-bedroom.
    "2833-chippendale-road-rodgers-creek-area": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 12,
         "unit_mix": {"3-bed": 10}, "floor_area": 1755.0, "floor_area_unit": "m2",
         "zoning_density": "FAR 0.6"},
        {"title": "Report to Council: June 5, 2023", "url": "https://westvancouver.ca/media/2330"},
    ),
    # 4020 Marine Drive: a Minor DVP adding one coach house.
    "4020-marine-drive-minor-development-variance-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "unit_mix": {"coach-house": 1}},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/4020-marine-drive-plans-updated-sep-2026.pdf"},
    ),
    # 4310 Marine Drive (Ferndale Townhomes): RS4 -> CD multi-family, 9 units (8x 3-bed, 1x 2-bed),
    # 1,863.8 m2 GFA, FAR 0.86, 3 levels, lot 2,177.4 m2, 18 vehicle + 18 bike stalls.
    "4310-marine-drive": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 9,
         "unit_mix": {"3-bed": 8, "2-bed": 1}, "number_of_stories": 3, "floor_area": 1863.8,
         "floor_area_unit": "m2", "lot_area": 2177.4, "lot_area_unit": "m2",
         "zoning_density": "FAR 0.86", "parking_vehicle_stalls": 18, "parking_bike_stalls": 18},
        {"title": "Architectural plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Architectural%20Drawings%20-%204310%20Marine%20Dr.pdf"},
    ),
    # 6330 & 6332 Argyle Avenue: six new ground-oriented residential units in three blocks;
    # ~9,321.55 sq ft (866 m2), FSR 0.714.
    "6330-6332-argyle-avenue-minor-development-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 6,
         "number_of_stories": 3, "floor_area": 866.0, "floor_area_unit": "m2",
         "zoning_density": "FSR 0.714"},
        {"title": "Proposed Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/6330%20Argyle%20250416-COM.pdf"},
    ),
    # 3190 Travers Avenue (Roy Residence): RS4 single-family dwelling, site 1,578.42 m2; the Minor
    # DVP is for waterfront accessory structures (terraces/trellis) - the existing house is unchanged.
    "3190-travers-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "lot_area": 1578.42, "lot_area_unit": "m2", "zoning_density": "RS4"},
        {"title": "Plans", "url": "https://westvancouver.ca/media/12117"},
    ),
    # --- batch 3: approved-plan / report data sheets (project data read in-session) ---
    # 1045 Aubeneau Crescent (Chu Residence, detached secondary suite DP): RS3, lot 979.17 m2; an
    # existing principal dwelling plus a new detached secondary suite (2 units). FAR floor area:
    # main house 293.88 m2 + suite 62.33 m2 = 356.2 m2; site coverage 28.71%.
    "1045-aubeneau-crescent-coach-house-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "secondary-suite": 1}, "floor_area": 356.2,
         "floor_area_unit": "m2", "floor_area_ratio": 0.36, "lot_area": 979.17,
         "lot_area_unit": "m2", "zoning_density": "RS3"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1045%20Aubeneau%20Crescent%20Plans%20%281%29.pdf"},
    ),
    # 1253 Haywood Avenue (Jimmy Yee Residence, coach-house DP): RS5, lot 778.25 m2; an existing
    # principal dwelling plus a new coach house (2 units). Floor area: principal 302.4 m2 + coach
    # house 51.8 m2 = 354.2 m2; proposed site coverage 262.5 m2.
    "1253-haywood-avenue-coach-house-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "coach-house": 1}, "floor_area": 354.2,
         "floor_area_unit": "m2", "lot_area": 778.25, "lot_area_unit": "m2",
         "zoning_density": "RS5"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Jimmy%20Yee%20-%20BP%20Drawings%20-%20JAN%2022%20REV.pdf"},
    ),
    # 380 Klahanie Court (preliminary): a BC Housing / Klahanee Park Housing Society / Darwin
    # redevelopment replacing 56 seniors' + 32 family homes with 611 purpose-built rental homes in
    # three buildings - a 24-storey market tower (347 units) + two 6-storey non-market buildings
    # (169 seniors + 95 family); shared underground parkade of 407 stalls.
    "380-klahanie-court": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 611,
         "unit_mix": {"market-rental": 347, "non-market-seniors": 169, "non-market-family": 95},
         "number_of_stories": 24, "parking_vehicle_stalls": 407, "rental_or_strata": "rental"},
        {"title": "Preliminary Development Proposal",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Preliminary%20Development%20Proposal%20-%20380%20Klahanie%20Court.pdf"},
    ),
    # 6408, 6412 & 6418 Bay Street (Trolls Site, preliminary): rezone C1 -> CD for a 5-storey mixed
    # retail + hotel building (42 hotel rooms) with underground parking; combined site 1,700 m2,
    # gross floor area 3,118.8 m2 (FAR 2.4), height 19 m, 44 vehicle + 34 bike stalls.
    # Storeys: kept as 4 to match the HTML ("four-storey building with a partial 5th floor for
    # rooftop amenities"); the project profile's "5" counts the partial floor (retained in the note).
    "6408-6412-6418-bay-street": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "number_of_stories": 4, "floor_area": 3118.8, "floor_area_unit": "m2",
         "floor_area_ratio": 2.4, "lot_area": 1700.0, "lot_area_unit": "m2", "height_m": 19.0,
         "parking_vehicle_stalls": 44, "parking_bike_stalls": 34,
         "zoning_density": "C1 -> CD, FAR 2.4"},
        {"title": "Staff report",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/26may11-6.pdf"},
    ),
    # 6588 Royal Avenue (Horseshoe Bay Apartment, former motel site): rezone C-1 -> CD for a 4-storey
    # 27-unit apartment; lot 1,184.2 m2, gross floor area 2,603.25 m2 (FAR 2.2), footprint 738.81 m2
    # (77% coverage), 25 vehicle + 38 bike stalls.
    "6588-royal-avenue-horseshoe-bay-motel-site": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 27,
         "number_of_stories": 4, "floor_area": 2603.25, "floor_area_unit": "m2",
         "floor_area_ratio": 2.2, "lot_area": 1184.2, "lot_area_unit": "m2",
         "footprint_area": 738.81, "parking_vehicle_stalls": 25, "parking_bike_stalls": 38,
         "zoning_density": "C-1 -> CD, FAR 2.2"},
        {"title": "Architectural plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Architectural%20Plans.pdf"},
    ),
    # 1135 Keith Road (coach-house DP): an existing residence retained plus a new coach house (2
    # units). The drawing set is a site plan / elevations only (no project-data table), so only the
    # class and unit count are read.
    "1135-keith-road-coach-house-development-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "coach-house": 1}},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Keith%20Road.pdf"},
    ),
    # 1281 Jefferson Avenue (coach-house DP): RS5, lot 747.65 m2 (8,048 sq ft); an existing residence
    # plus a new 91.04 m2 (980 sq ft) coach house (2 units); total proposed floor area 359.58 m2
    # (FSR 0.483), coach-house height 3.66 m.
    "1281-jefferson-avenue-coach-house-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "coach-house": 1}, "floor_area": 359.58,
         "floor_area_unit": "m2", "floor_area_ratio": 0.483, "lot_area": 747.65,
         "lot_area_unit": "m2", "zoning_density": "RS5"},
        {"title": "Approved Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1281%20Jefferson%20Ave%20-%20Approved%20Plans.pdf"},
    ),
    # 338 Moyne Drive: a new single-family dwelling with a secondary suite (2 units) plus an attached
    # garage and a pool change room, on a watercourse-setback lot. Single-sheet site plan (no
    # project-data table), so only the class and unit count are read.
    "338-moyne-drive": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "secondary-suite": 1}},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/338-moyne-drive-approved-plans.pdf"},
    ),
    # 1018 Pound Road (Youth Hub, DVP): a permanent community Youth Hub building in the southeast
    # corner of Ambleside Park, on the footprint of the former Ambleside Youth Centre (closed 2019,
    # burned down). A civic/community recreation facility - institutional. The report gives no
    # building dimensions, so only the class is read.
    "1018-pound-road-youth-hub": (
        {"development_class": "institutional", "occupancy_types": ["institutional"]},
        {"title": "Council report",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/26aug24-5.3.pdf"},
    ),
    # 5768-B Larson Place (Stewart-Lough House, Heritage Revitalization Agreement): RS4 site split
    # into Lot A (retained heritage house, 337.33 m2) and Lot B (new infill house, 474.92 m2) - two
    # single-family dwellings; combined lot 1,423.6 m2, combined floor area 812.25 m2, 2 storeys.
    "5768-larson-avenue-stewart-lough-house": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"heritage-house": 1, "infill-house": 1}, "number_of_stories": 2,
         "floor_area": 812.25, "floor_area_unit": "m2", "lot_area": 1423.6, "lot_area_unit": "m2",
         "zoning_density": "RS4 (Heritage Revitalization Agreement)"},
        {"title": "Preliminary Drawings",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Arch%20%26%20Landscape%20Plans%20-%205768%20Larson%20Place.pdf"},
    ),
    # --- batch 5: remaining flagged rows with a readable report / data sheet ---
    # 640 Burley Drive (Kavanagh Coach House, DP-25-112): an existing 2-level dwelling plus a new
    # detached secondary suite (2 units); site 12,833 sq ft (1,192.2 m2); combined gross floor area
    # 4,444.94 sq ft (412.95 m2), coach-house height 4.75 m.
    "DP-25-112": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "secondary-suite": 1}, "floor_area": 412.95,
         "floor_area_unit": "m2", "lot_area": 1192.2, "lot_area_unit": "m2", "height_m": 4.75},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/dp-25-112-640-burley-drive-arch-drawings-2026-05-28.pdf"},
    ),
    # 835 Andover Crescent (Liu Residence, DVP25-023): RS3, renovation of the existing single-family
    # dwelling with an upper-floor addition (1 unit); lot 15,470.5 sq ft (1,437.2 m2), proposed floor
    # area 5,218 sq ft (484.8 m2).
    "DVP25-023": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "floor_area": 484.8, "floor_area_unit": "m2", "lot_area": 1437.2, "lot_area_unit": "m2",
         "zoning_density": "RS3"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/DVP25-023%20-%20835%20Andover%20Crescent%20-%20Drawings%20%281%29.pdf"},
    ),
    # 2272 Jefferson Avenue (DP22-085): RS5, an existing house plus a new 2-storey coach house (2
    # units); lot 755.58 m2, proposed FAR floor area 298.32 m2 (0.395), coach-house height 6.40 m.
    "DP22-085": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "coach-house": 1}, "number_of_stories": 2,
         "floor_area": 298.32, "floor_area_unit": "m2", "floor_area_ratio": 0.395,
         "lot_area": 755.58, "lot_area_unit": "m2", "height_m": 6.40, "zoning_density": "RS5"},
        {"title": "Approved plans (variance)",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/2272-jefferson-avenue-approved-plans-variance.pdf"},
    ),
    # 2550 Queens Avenue (rezoning + DVP 23-055): RS3 site subdivided into Lot A ("Mary", 493.1 m2)
    # and Lot B ("Doreen", 618.5 m2); one single-family dwelling with an abutting secondary suite on
    # each lot (4 dwelling units), two-car garage each.
    "2550-queens-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 4,
         "unit_mix": {"detached": 2, "secondary-suite": 2}, "lot_area": 1111.6,
         "lot_area_unit": "m2", "zoning_density": "RS3"},
        {"title": "Memo",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/25may12-8.pdf"},
    ),
    # 5051 Howe Sound Lane (DP13-070): RS3 subdivision of a 3,775 m2 property into Lot A (2,348 m2,
    # existing house retained) and Lot B (1,427 m2, vacant) - two single-family lots.
    "DP13-070": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"detached": 2}, "lot_area": 3775.0, "lot_area_unit": "m2",
         "zoning_density": "RS3"},
        {"title": "Council Report for DP13-070",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/DWV-%2523927778-v1-COUNCIL_REPORT_2015_03_30_DEVELOPMENT_PERMIT_APPLICATION_NO__13-070_FOR_5051_HOWE_SOUND_LANE_.pdf"},
    ),
    # 4777 Pilot House Road (Heritage Alteration Permit): RS3, one single-family dwelling + an
    # accessory garage; lot 1,388.6 m2, principal net floor area 409.5 m2 (FAR 0.29), 1 storey +
    # basement, height 5.65 m, 2 parking.
    "heritage-alteration-permit-4777-pilot-house-road": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "number_of_stories": 1, "floor_area": 409.5, "floor_area_unit": "m2",
         "floor_area_ratio": 0.29, "lot_area": 1388.6, "lot_area_unit": "m2", "height_m": 5.65,
         "parking_vehicle_stalls": 2, "zoning_density": "RS3"},
        {"title": "Approved Architectural Plans Part 1",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1.%20Architectural%20Plans%20-%20Part%201.pdf"},
    ),
    # --- batch 6: image-only drawing sets (titleblock / survey plans read from the rendered image) ---
    # 1776 Inglewood Avenue (Villavicencio Custom Residence, EDP26-045): a new single-family dwelling
    # (1 unit) on a creek-side lot.
    "EDP26-045": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/approved-plans-1776-inglewood-may-26-2026.pdf"},
    ),
    # 1776 Inglewood Avenue (Villavicencio Custom Residence, Heritage Alteration Permit HAP24-051):
    # the same new single-family custom residence (1 unit); companion to EDP26-045.
    "heritage-alteration-permit-1776-inglewood-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/SCHEDULE%20A%20to%20HAP24-051%20Architectural%20Plans%20prepared%20by%20Synthesis%20Design%20dated%20January%2013_%202025.pdf"},
    ),
    # 2475 Palmerston Avenue (EDP26-008): an environmental DP for a proposed retaining wall (Flex MSE /
    # lock-block) at an existing single-family residence beside Marr Creek. No new dwelling.
    "EDP26-008": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/2475-palmerston-av-approved-plans.pdf"},
    ),
    # 2650 Bellevue Avenue (DP26-005): a new foreshore concrete retaining wall at an existing
    # waterfront residence. No new dwelling.
    "DP26-005": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/2650-bellevue-avenue-approved-plans.pdf"},
    ),
    # 430 Macbeth Crescent (Steep Slope / Minor DP): a two-lot subdivision (one panhandle lot), each
    # with a concept dwelling; combined 30,755 sq ft (2,857.3 m2).
    "430-macbeth-crescent-minor-development-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"detached": 2}, "lot_area": 2857.3, "lot_area_unit": "m2"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/430-macbeth-crescent-approved-plans.pdf"},
    ),
    # 4437 Stone Court (Richardson Residence, EDP variance): a renovation/addition to the existing
    # single-family dwelling (1 unit).
    "4437-stone-court-edp-variance": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1},
        {"title": "Approved Architectural Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/SCHEDULE%20A%20to%20EDP%2021-089_Architectural%20Plans_March%2017%202025.pdf"},
    ),
    # 4763 Pilot House Road: a proposed accessory building at an existing single-family dwelling
    # (existing garage removed). 1 dwelling unit.
    "4763-pilot-house-road": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/4763%20Pilot%20House%20Road%20-%20Architectural%20Plans.pdf"},
    ),
    # 4375 Marine Drive: a proposed accessory building at an existing single-family dwelling beside
    # Cypress Creek. 1 dwelling unit.
    "4375-marine-drive": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/4375-marine-drive-approved-plans.pdf"},
    ),
    # 931 Esquimalt Avenue (subdivision): RS5 lot (~1,470.2 m2) subdivided into three single-family
    # lots of 490.1 m2 each; the existing dwelling is to be demolished.
    "931-esquimalt-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 3,
         "unit_mix": {"detached": 3}, "lot_area": 1470.2, "lot_area_unit": "m2",
         "zoning_density": "RS5"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/931-esquimalt-approved-plans.pdf"},
    ),
    # 5365 Kensington Crescent (Difficult Terrain subdivision): Lot 19 subdivided into Lot A
    # (1,101.7 m2) and Lot B (1,101.2 m2) - two single-family lots.
    "5365-kensington-crescent-sites-difficult-terrain": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"detached": 2}, "lot_area": 2202.9, "lot_area_unit": "m2"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/5365%20Kensington%20Crescent%20-%2024-036%20Concept%20Plans.pdf"},
    ),
    # 1449 Fulton Avenue (environmental DP + interior renovation + legal suite): a secondary (legal)
    # suite added to the existing single-family dwelling (2 units); lot 8,116 sq ft (754.0 m2).
    "1449-fulton-avenue-edp-variances": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "secondary-suite": 1}, "lot_area": 754.0,
         "lot_area_unit": "m2"},
        {"title": "Approved plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1449%20Fulton%20Ave%20Plans.pdf"},
    ),
    # 5665 Westport Road (bare-land strata subdivision): a 67,859 sq ft (6,304.3 m2) site subdivided
    # into four bare-land strata lots (SL1-SL4), each a concept single-family dwelling, with shared
    # common property and an access route.
    "5665-westport-road": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 4,
         "unit_mix": {"detached": 4}, "lot_area": 6304.3, "lot_area_unit": "m2",
         "rental_or_strata": "strata"},
        {"title": "Approved plan",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Proposed%204%20Lot%20Bare%20Land%20Strata%20Subdivision%20Plan.pdf"},
    ),
    # 2816 Bellevue Avenue: rezoning of the southern (foreshore) portion from M1 to RS4; a waterfront
    # property of 1,355.4 m2 with an existing single-family dwelling, detached garage and pool.
    "2816-bellevue-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "lot_area": 1355.4, "lot_area_unit": "m2", "zoning_density": "M1 -> RS4"},
        {"title": "Council report",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/25dec15-9.3.pdf"},
    ),
    # 4444 Stone Crescent (Difficult Terrain / Wildfire DP): RS3 subdivision of Lot 2 into Proposed A
    # (1,343.8 m2) and Proposed B (2,333.4 m2), each with a proposed dwelling - two single-family lots.
    "4444-stone-crescent-difficult-terrain-wildfire-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"detached": 2}, "lot_area": 3677.2, "lot_area_unit": "m2",
         "zoning_density": "RS3"},
        {"title": "Approved Plan",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/4444%20Stone%20Crescent%20-%20Proposed%20plans.pdf"},
    ),
}

# Per-building detail appended to raw_text for the multi-building applications.
BREAKDOWNS = {
    "3033-3055-marine-drive-townhouses": (
        "Architect PROJECT DATA (The Creek, 3033 & 3055 Marine Drive): proposed CD zone (from RS2); "
        "lot 73,294.7 sq ft (6,809 m2); FAR 24,555.8 sq ft (FSR 0.33); building height 25 ft "
        "(7.6 m); parking 22 underground (2/unit) + 5 visitor + 22 bike; salable area 2,267.3 m2 "
        "across 3 blocks (A 851.7 / B 595.2 / C 820.4 m2, main + upper floor each)."
    ),
    "875-885-jefferson-avenue": (
        "Proposed four single-family lots: 875-A 565.3 m2, 875-B 575.7 m2, 885-A 544.5 m2, "
        "885-B 554.8 m2 (RS3 minimum is 975 m2). Allowable floor area ~204.4 m2 per 875 lot and "
        "~200.7 m2 per 885 lot."
    ),
    "2810-2814-2818-bellevue-avenue": (
        "Floor area by address: 2810 Bellevue 228.2 m2 (2,456 sq ft); 2814 Bellevue 283.7 m2 "
        "(3,054 sq ft); 2818 Bellevue 399.8 m2 (4,304 sq ft)."
    ),
    "2859-bellevue-avenue-house-coach-house": (
        "Project data (Men/Peever Residence, 2859 Bellevue Avenue, RS4 new construction): site "
        "16,741.90 sq ft (1,555.37 m2); FAR permitted 0.35 (544.38 m2) / proposed 0.28 (429.43 m2 / "
        "4,622.39 sq ft); site coverage 20.3%; principal house 2 storeys + basement, coach house "
        "1 storey + basement; 3 off-street parking (2 principal + 1 coach house)."
    ),
    "1045-aubeneau-crescent-coach-house-dp": (
        "Project data (Chu Residence, 1045 Aubeneau Crescent, RS3 detached secondary suite DP): lot "
        "10,540 sq ft (979.17 m2); FAR floor area main house 293.88 m2 + detached secondary suite "
        "62.33 m2 (basement 55.67 + main 67.31); FAR 0.3 plus 800 sq ft suite bonus; site coverage "
        "281.14 m2 (28.71%); suite building height 4.568 m."
    ),
    "1253-haywood-avenue-coach-house-dp": (
        "Project data (Jimmy Yee Residence, 1253 Haywood Avenue, RS5 coach-house DP): lot 8,377 sq ft "
        "(778.25 m2); principal dwelling 302.4 m2 (3,255.6 sq ft: upper 1,106 / main 1,336 / basement "
        "813.6 sq ft), FSR 0.3 (allowed 233 m2); coach house 51.8 m2, height 4.54 m; proposed site "
        "coverage 262.5 m2 (allowed 266 m2)."
    ),
    "380-klahanie-court": (
        "Proposed buildings (380 Klahanie Court, BC Housing / Klahanee Park Housing Society / Darwin): "
        "a 24-storey market-rental tower on podium (347 units); a 6-storey non-market Seniors' building "
        "(169 units) and a 6-storey non-market Family building (95 units), owned/operated by BC Housing "
        "and KPHS - 611 rental homes total (264 non-market). Shared underground parkade 407 stalls "
        "(0.67/unit). Replaces the existing 56 seniors' apartments + 32 family townhomes (existing GFA "
        "70,278 sq ft, FAR 0.49)."
    ),
    "6408-6412-6418-bay-street": (
        "Project profile (6408/6412/6418 Bay Street, Trolls Site, File 25-099): combined site 1,700 m2 "
        "(18,299 sq ft) from three C1 lots; proposed CD, 5 storeys (4 + partial 5th), gross floor area "
        "3,118.8 m2 (43,031.5 sq ft), FAR 2.4, height 19 m; 42 hotel rooms; parking 44 (43 underground "
        "+ 1 accessible at lane), 34 bike stalls, Level 2 EV."
    ),
    "6588-royal-avenue-horseshoe-bay-motel-site": (
        "Data sheet (Horseshoe Bay Apartment, 6588 Royal Avenue, former motel site): rezone C-1 -> CD; "
        "RESIDENTIAL (Group C), 4 storeys, 27 units; lot 12,746 sq ft (1,184.2 m2); gross floor area by "
        "level 733.88 / 738.81 / 658.52 / 472.05 m2 = 2,603.25 m2 (FAR 2.198); lot coverage 908.05 m2 "
        "(77%); parking proposed 25 (1 accessible / 17 regular / 7 small, required 27), 38 secure bike."
    ),
    "DP-25-112": (
        "Zoning analysis (640 Burley Drive, Kavanagh Coach House): total site 12,833 sq ft; main "
        "house gross floor area 3,004.5 sq ft (FSR 2,563.93), detached secondary suite gross 1,440.44 "
        "sq ft (FSR 640.44, after 800 sq ft exclusion); total net site FSR 3,204.37 sq ft; site "
        "coverage 3,539.55 sq ft; both dwellings 3-bedroom; 1 existing parking space."
    ),
    "DP22-085": (
        "Site/project data (2272 Jefferson Avenue, RS5): lot 755.58 m2 (8,133 sq ft); max FAR "
        "allowable 300.98 m2 (226.68 + 74.3 coach-house bonus), existing 170.12 m2, proposed 298.32 "
        "m2; coach house combined 148.60 m2 (main floor 109.90 + upper 38.69), 2 storeys, height 6.40 "
        "m; site coverage proposed 206.54 m2 (27.3%)."
    ),
    "2550-queens-avenue": (
        'Proposed subdivision (2550 Queens Avenue, RS3 zoning amendment + DVP 23-055): Lot A "Mary" '
        '493.1 m2 and Lot B "Doreen" 618.5 m2 (RS3 minimum 975 m2); each lot gets one single-family '
        "dwelling with one abutting secondary suite and a two-car garage off the rear lane."
    ),
    "DP13-070": (
        "Proposed subdivision (5051 Howe Sound Lane, RS3): the 3,775 m2 property split into Lot A "
        "2,348 m2 (existing two-storey house + driveway retained, permitted floor area 800 m2) and "
        "Lot B 1,427 m2 (vacant, 499 m2). RS3 minimum lot size 1,115 m2."
    ),
    "heritage-alteration-permit-4777-pilot-house-road": (
        "Site data (4777 Pilot House Road, RS3 Heritage Alteration Permit): one single-family dwelling "
        "unit + one accessory garage; site 1,388.6 m2 (14,946.6 sq ft); principal net floor area 409.5 "
        "m2 (FAR 0.29), 1 storey + basement, height 5.65 m; garage net 7.1 m2, 1 storey + crawl space; "
        "lot coverage 30%; 2 parking spaces."
    ),
    "1281-jefferson-avenue-coach-house-dp": (
        "Bylaw review (1281 Jefferson Avenue, RS5 coach-house DP): lot 8,048 sq ft (747.65 m2); "
        "existing residence total living area 2,860.9 sq ft (lower 1,280.9 + upper 1,612.9, plus "
        "440 sq ft garage), existing FAR 35.9%; proposed coach house 980 sq ft (91.04 m2), height "
        "12 ft (3.66 m); total proposed living area 3,870.9 sq ft (359.58 m2), FAR 48.3%."
    ),
    "430-macbeth-crescent-minor-development-permit": (
        "Steep-slope subdivision (430 Macbeth Crescent): proposed Lot A gross 14,930 sq ft (1,387.1 "
        "m2, net 977.6 m2) and Lot B gross 15,825 sq ft (1,470.2 m2, net 1,113.7 m2) with one "
        "panhandle access; each lot carries a concept dwelling + garage. A Steep Slope DP is required."
    ),
    "931-esquimalt-avenue": (
        "Proposed subdivision (931 Esquimalt Avenue, RS5): the lot is divided into three single-family "
        "lots of 5,275 sq ft (490.1 m2) each; the existing dwelling (931 Esquimalt) is to be "
        "demolished. Square width 102.55 ft (31.257 m)."
    ),
    "5365-kensington-crescent-sites-difficult-terrain": (
        "Proposed subdivision (5365 Kensington Crescent, Block H DL 879): Lot 19 divided into Lot A "
        "0.110 ha (1,101.7 m2) and Lot B 0.110 ha (1,101.2 m2) - two single-family lots."
    ),
    "5665-westport-road": (
        "Bare-land strata subdivision (5665 Westport Road, total 67,859 sq ft / 6,304.3 m2): four "
        "strata lots - SL1 833.8 m2 (gross 1,137.3), SL2 759.1 m2 (gross 1,035.5), SL3 902.7 m2 "
        "(gross 1,231.3), SL4 1,071.8 m2 (gross 1,462.0) - each a concept single-family dwelling, "
        "with common-property driveways, an access route and creek environmental-protection areas."
    ),
    "4444-stone-crescent-difficult-terrain-wildfire-dp": (
        "Proposed subdivision (4444 Stone Crescent, Lot 2 Block H DL 582, RS3): Proposed A 14,465 sq "
        "ft (1,343.8 m2) and Proposed B 25,117 sq ft (2,333.4 m2, net buildable 10,652 sq ft / 989.6 "
        "m2), each with a proposed dwelling. A Difficult Terrain / Wildfire DP applies."
    ),
    "1449-fulton-avenue-edp-variances": (
        "Environmental DP + interior renovation + legal suite (1449 Fulton Avenue): a secondary (legal) "
        "suite is added to the existing single-family dwelling, with interior renovation; major "
        "occupancy Residential C; lot 8,116 sq ft (754.0 m2), creek/top-of-bank setbacks apply."
    ),
    "EDP26-008": (
        "Retaining wall (2475 Palmerston Avenue, EDP26-008): replacement of the west-bank wood "
        "retaining wall with a Flex MSE / lock-block wall beside Marr Creek; proposed wall heights "
        "85.25-86.20 m (DVW bylaw limits 84.20-86.70 m). The existing dwelling is unchanged."
    ),
    "DP26-005": (
        "Retaining wall (2650 Bellevue Avenue, DP26-005): a new foreshore concrete retaining wall at "
        "the existing waterfront residence (structural drawings only); no new dwelling or floor area."
    ),
    "4763-pilot-house-road": (
        "Accessory building (4763 Pilot House Road): a proposed accessory building at the existing "
        "single-family dwelling; the existing garage is to be removed."
    ),
    "4375-marine-drive": (
        "Accessory building (4375 Marine Drive): a proposed accessory building (top of roof el. 48.1) "
        "at the existing single-family dwelling, between the house and the Cypress Creek top-of-bank."
    ),
    "2816-bellevue-avenue": (
        "Waterfront rezoning (2816 Bellevue Avenue, M1 -> RS4 for the southern portion): a 1,355.4 m2 "
        "(14,589 sq ft) waterfront property with an existing single-family dwelling, detached garage "
        "and pool/lawn; an existing buried lock-block wall with riprap provides foreshore protection."
    ),
    "5768-larson-avenue-stewart-lough-house": (
        "Zoning table (Stewart-Lough House, 5768-B Larson Place, RS4 Heritage Revitalization Agreement): "
        "Lot A retained heritage house - site 722.3 m2, floor area 337.33 m2 (46.7%), site coverage "
        "282.98 m2, 2 storeys + basement, height 5.97 m. Lot B new infill house - site 701.3 m2, floor "
        "area 474.92 m2 (67.7%), site coverage 301.56 m2, 2 storeys, height 7.4 m."
    ),
    "3181-3389-chippendale-rd-3861-3875-uplands-way-beedie": (
        "Proposed buildings: Lot 1 / 3181 Chippendale Rd - 20 storeys, ~108,000 sq ft, 63 strata "
        "units; Lot 9 / 3389 Chippendale Rd - 25 storeys, ~150,000 sq ft, 93 strata units; Lot 12 "
        "/ 3875 Uplands Way - 16 storeys, ~111,000 sq ft, 81 strata units (converted from rental); "
        "Lot 13 / 3861 Uplands Way - 20 storeys, ~136,000 sq ft, 87 strata units over a shared parkade."
    ),
}


# HTML fields to clear before enrichment because the regex misfired on a zoning LIMIT rather than
# the proposal (so the Claude-read proposal value fills cleanly instead of losing to a bad parse).
OVERRIDE_HTML = {
    "3181-3389-chippendale-rd-3861-3875-uplands-way-beedie": ["number_of_stories"],  # "16-storey limit" -> 25
    "6330-6332-argyle-avenue-minor-development-permit": ["number_of_stories"],
    # HTML read 0.11 m (the requested height variance overage) as the building height; the coach
    # house is 6.40 m, so the bad parse is cleared and the PDF value fills.
    "DP22-085": ["height_m"],
}


def main() -> None:
    """Apply the Claude-read fields to data/processed/west_van.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "west_van.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        for field in OVERRIDE_HTML.get(permit_id, []):
            row.pop(field, None)
            (row.get("field_methods") or {}).pop(field, None)
        filled = wv.apply_claude_pdf(row, fields, document)
        note = BREAKDOWNS.get(permit_id)
        # Append the per-building note once; guard so re-running the script never duplicates it.
        if note and note not in (row.get("raw_text") or ""):
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
