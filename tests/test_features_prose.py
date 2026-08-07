"""Prose-parsing regression tests built from real CNV applications.

Each case is the actual raw_text of an application whose fields the deterministic
parser originally missed (QA'd at extraction_confidence <= 0.4). They lock in the
generalized unit / parking / storey / floor-area logic in bc_dev_permits.features.
Prose is embedded verbatim - do NOT edit it to make a test pass; fix the parser.

Regenerate:  python tests/gen (see scratchpad) — or edit CASES by hand.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits import features  # noqa: E402

# (label, prose, expected-field-subset, unit_mix-subset-or-None)
CASES = [
    (
        '400 East 1st Street',
        'Shida Neshat-Behzadi Architects have submitted a Development Permit application for a six unit development on the subject site. The proposal includes six principal units and six lock-off suites, along with three vehicle parking stalls.',
        {'units_total': 6, 'parking_vehicle_stalls': 3, 'development_class': 'residential'},
        {'principal': 6, 'lock-off': 6},
    ),
    (
        '835 - 845 West 15th Street',
        'Cascadia Green Development has submitted an application to rezone the properties at 835-845 W 15th Street to allow construction of a five-storey retail-service building consisted of retail services on the ground floor and non-surgical medical offices above with two levels of underground parking having access from the rear lane. The proposal includes 41 car parking spaces, 2 of which are accessible, two loading spaces, and a rooftop outdoor patio. Your comments will be shared with City staff and the Applicant to help shape the proposal through the review process. Comments will not be posted publicly or shared with Council. For information on how to provide feedback during the Council process, visit cnv.org/CouncilMeetings . Which of the following best describes you: I live in the City of North Vancouver I work in the City of North Vancouver I live AND work in the City of North Vancouver None of the above Please provide your contact info if you would like a response to your feedback. Your contact info will be shared with the Applicant but not with Council. The City is collecting your personal information in accordance with Section 26(c) of the Freedom of Information and Protection of Privacy Act. The City collects your information for the purposes of administering City programs and services, including permits and licensing services. If you have any questions, please contact the Privacy Coordinator at 141 West 14th Street, North Vancouver, BC V7M 1H9 or planning@cnv.org or 604-985-7761.',
        {'number_of_stories': 5, 'parking_vehicle_stalls': 41, 'development_class': 'commercial'},
        None,
    ),
    (
        '311 Moody Avenue',
        'MA Architects Ltd, has submitted a Development Permit Application on behalf of 311 Moody Holding to the City of North Vancouver for 311 Moody Ave to allow for the development of a 5 unit townhouse development with lock off units.',
        {'units_total': 5, 'development_class': 'residential'},
        {'townhouse': 5},
    ),
    (
        '365 West 19th Street',
        'Christopher Vaissade (CV Designs) has applied to rezone the property from RS-1 to RS-2 to allow for subdivision into two lots and the development of two single-family homes. Each new lot will have one principal dwelling unit, one accessory secondary suite, and two vehicle parking stalls. Christopher Vaissade CV Designs Telephone: 604-614-6627 Email: chris@cvdesigns.ca',
        {'units_total': 2, 'development_class': 'residential'},
        None,
    ),
    (
        '442-444 East 1st Street',
        'Moodyville Development Permit Application for a fourplex townhouse with lock-off units. Reza Salehi Salehi Architect Inc. Telephone: 778-996-7833 Email: rsalehi@salehiarchitect.ca',
        {'units_total': 4, 'development_class': 'residential'},
        None,
    ),
    (
        '229-231 West 15th Street',
        'Wein & Associates has submitted a rezoning application to permit the development of a five unit multi-family building with five parking stalls. Wein & Associates Telephone: 604-727-3764 Email: KARLWEIN@GMAIL.COM',
        {'units_total': 5, 'parking_vehicle_stalls': 5, 'development_class': 'residential'},
        None,
    ),
    (
        '509 East 6th Street',
        'Vernacular Group has applied for a rezoning from RS-1 to RS-2 at 509 East 6th Street. This would permit a subdivision from one to two lots and the construction of a new single family home on each lot. Your comments will be shared with City staff and the Applicant to help shape the proposal through the review process. Comments will not be posted publicly or shared with Council. For information on how to provide feedback during the Council process or Public Hearing, visit cnv.org/PublicHearings . Which of the following best describes you: I live in the City of North Vancouver I work in the City of North Vancouver I live AND work in the City of North Vancouver None of the above Please provide your contact info if you would like a response to your feedback. Your contact info will be shared with the Applicant but not with Council. The City is collecting your personal information in accordance with Section 26(c) of the Freedom of Information and Protection of Privacy Act. The City collects your information for the purposes of administering City programs and services, including permits and licensing services. If you have any questions, please contact the Privacy Coordinator at 141 West 14th Street, North Vancouver, BC V7M 1H9 or planning@cnv.org or 604-985-7761.',
        {'units_total': 2},
        None,
    ),
    (
        '602-632 East 2nd Street',
        'The City has received an application for a 60 unit townhouse development on two separate sites along the north side of East 2nd Street. They are arranged in a stacked configuration in four rows of buildings that are four storeys tall, separated by a central courtyard. The buildings are on top of two levels of underground parking to provide 90 parking stalls.',
        {'units_total': 60, 'number_of_stories': 4, 'parking_vehicle_stalls': 90, 'development_class': 'residential'},
        {'townhouse': 60},
    ),
    (
        '229 East 22nd Street',
        'Nanak ventures Inc. has applied to rezone the property from RS-1 to RT-1 to allow for the construction of a new duplex. Each duplex will have an accessory dwelling unit, and a total of four parking spaces will be provided with access off the lane. Telephone: 604-924-4663 Email: INFO@BOLDERHOMES.CA',
        {'units_total': 2, 'parking_vehicle_stalls': 4, 'development_class': 'residential'},
        None,
    ),
    (
        '63 Mahon Avenue',
        'The City of North Vancouver has received a rezoning application from Lamoureux Architecture Inc. for 63 Mahon Avenue. The application is for a text amendment to the existing CD-684 Zone to allow for an additional floor to a two and one-half storey private school (Alcuin College) building that was approved by City Council in February 2017. Alcuin College is a K-12 private school following the Ministry of Education’s curriculum with a student cap of 200 students for the new facility. The intended use of the added fourth floor is for a shared amenity space between Alcuin College during school hours, and for community, cultural groups and events up to 150 people outside of school hours. As part of the rezoning application, the applicant has offered this space free of charge to North Shore community and cultural groups to hold meetings and occasional gatherings. Eleven off-street parking spaces are available on the site in accordance with the 2017 approval, and an additional eleven off-street parking stalls are proposed at a location at 132 West Esplanade (approximately 450 meters from the site). The applicant has secured a vehicle to transport those to and from the school site who require it. Consistent with the City’s standard process, an excavation permit has been issued based on the current two and one-half storey building. However, no Building Permit has been issued for the site. David Johnson Planning Lead Telephone: 604-983-7357 Email: planning@cnv.org Brad Lamoureux Lamoureux Architect Inc. Telephone: 604-925-5170 Email: brad@lamoureuxarchitect.ca',
        {'parking_vehicle_stalls': 11, 'development_class': 'institutional'},
        None,
    ),
    (
        '133 East 4th Street',
        'The City of North Vancouver has received an application to rezone 133 East 4th Street for the purpose of developing a six storey, 23 unit apartment building over a 236.9 square metre (2,550 square foot) child care facility that is accessed off of the rear lane. The application is proposing no resident parking stalls and two child care parking stalls off of the rear lane. David Johnson Planning Lead Telephone: 604-983-7357 Email: planning@cnv.org Barry Savage Three Shores Development Telephone: 604-505-8818 Email: bsavage@threeshoresdevelopment.com',
        {'units_total': 23, 'number_of_stories': 6, 'development_class': 'mixed'},
        {'apartment': 23},
    ),
    (
        '245 East 10th Street',
        'Heritage conservation and rezoning application to permit the addition of a duplex at the rear of the lot at 245 East 10th Street. The site has a heritage level A home at the front of the lot which will be restored and protected. The heritage home combined proposed infill development would result in a total of three principal units. James Stobie Synthesis Design Telephone: 587-834-5240 Email: james@synthesisdesign.ca',
        {'units_total': 3, 'development_class': 'residential'},
        None,
    ),
    (
        '341 West 24th Street',
        'Rezoning from RS-1 to RS-2 Zone which allows for a narrower lot frontage. This proposal would permit subdivision of the existing lot into two new single-family lots with suites. Huy Dang Planning Lead Telephone: 604-983-7357 Email: planning@cnv.org Bill Curtis Bill Curtis Design Telephone: 604-986-4550 Email: billcurtisdesign@gmail.com',
        {'units_total': 2},
        None,
    ),
    (
        '502 East 5th Street',
        'Rezoning for the development of two single-family units through a subdivision. A variance for parking is proposed for the west lot to allow for one parking space on a lot, with a Principal Dwelling and Secondary Suite. Huy Dang Lead Planner Telephone: 604-990-4216 Email: planning@cnv.org Mehrdad Rahbar Vernacular Group Telephone: 604-990-6662 Email: mrahbar@vernaculardev.com',
        {'units_total': 2, 'parking_vehicle_stalls': 1, 'development_class': 'residential'},
        None,
    ),
    (
        '548-558 East 1st Street',
        '“Trails 2A” Moodyville Development Permit, for 5 townhouse units and 39 apartment units. Grant Myles Wall Financial Corporation Telephone: 604-893-7223 Email: gmyles@wallcentre.com',
        {'units_total': 44, 'development_class': 'residential'},
        {'townhouse': 5, 'apartment': 39},
    ),
    (
        '2008 Westview',
        'The applicant has submitted an application to rezone the property from RS-1 to RS-2 in order to enable the future subdivision of the lot into two new lots. Each new lot will be permitted to construct one single-family home with an accessory secondary suite and two parking stalls. DJAMSHIED SHAKIRIN Telephone: 604-721-5201 Email: DJ.SHAKIRIN@GMAIL.COM',
        {'units_total': 2},
        None,
    ),
    (
        '427-429 & 433-435 East 3rd Street',
        'The City has received a Moodyville Development Permit application at 427-429 and 433-435 East 3rd Street, for a four-storey townhouse development. The proposal is for 15 units, and one adaptable lock-off unit. The application will be considered by Council, for two proposed relaxations to the Moodyville Development Permit Guidelines : The application was considered by Council on Monday, June 21, 2021. Helen Besharat BFA Studio Architects Telephone: 604-662-8544 Email: info@bfastudioarchitects.com',
        {'units_total': 15, 'number_of_stories': 4, 'development_class': 'residential'},
        None,
    ),
    (
        '2762 Lonsdale Avenue',
        'The City has received a development application from Adera Projects Ltd. for 2762 Lonsdale Avenue for a six-storey rental apartment building with 60 units, underground parking accessed from the lane, and rooftop and ground-floor amenity spaces. Sarah Bingham Adera Telephone: 604-684-8277 Email: sarahb@adera.com',
        {'units_total': 60, 'number_of_stories': 6, 'development_class': 'residential'},
        None,
    ),
    (
        '273 - 275 East 6th Street',
        'The City has received a development application from Jalil Astanehe for 273-279 East 6th Street to rezone the properties to support the development of 10 townhouse units. The proposal includes two rows of townhouse units over one level of underground parking for 11 vehicles. Kyle Pickett Planning Lead Telephone: 604-983-7357 Email: planning@cnv.org Hassan Moayeri Telephone: 604-985-2472 Email: hassanmoayeriarchitect@shaw.ca',
        {'units_total': 10, 'parking_vehicle_stalls': 11, 'development_class': 'residential'},
        {'townhouse': 10},
    ),
    (
        '341-347 West 4th Street',
        'Gradual Architecture Inc. has submitted an application to rezone the subject properties to allow the construction a six-storey residential building with 69 units of rental housing.',
        {'units_total': 69, 'number_of_stories': 6, 'development_class': 'residential'},
        None,
    ),
    (
        '1540 St Georges Avenue & 215-235 East 16th Street',
        'RED East 16th Limited Partnership and RED Twelve E16 Adera Projects Ltd. has submitted an application to amend the Zoning Bylaw to allow the construction of two six-storey residential buildings. There will be a total of 167 market rental units and 19 Inclusionary Housing units (mid-market rental), for a total of 186 units. Parking for vehicles and bicycles will be provided in one underground level.',
        {'units_total': 186, 'number_of_stories': 6, 'development_class': 'residential'},
        None,
    ),
    (
        '215 West Keith Road',
        'Golden Line Homes Ltd. has applied for a rezoning from the existing RT-1 (Two Unit Residential) zone to a new Comprehensive Development zone to allow for the construction of a three-unit townhouse development. Each townhouse unit will have one basement suite and one on-site parking stall.',
        {'units_total': 3, 'development_class': 'residential'},
        {'townhouse': 3},
    ),
    (
        '328 East 14th Street',
        'Pucci Properties LTD. has applied for a rezoning from the existing zone to a new Comprehensive Development zone to allow the construction of a 4-unit, with 4 lock-off units, and a detached unit at the rear of the lot. The design proposed 5 parking stalls. Pasquale Pucci Pucci Properties LTD. Telephone: 604-763-2580 Email: pucciproperties.east14th@gmail.com',
        {'units_total': 4, 'parking_vehicle_stalls': 5, 'development_class': 'residential'},
        {'lock-off': 4},
    ),
    (
        '422 East 1st Street',
        'Nadi Miri (m+ Architecture Inc) has applied for a Development Permit to allow the construction of four-unit townhouse building. Each townhouse will have one lock-off suite and one vehicle parking stall.',
        {'units_total': 4, 'development_class': 'residential'},
        {'townhouse': 4},
    ),
    (
        '226 West 5th Street',
        '1476941 BC LTD has applied for a Zoning Bylaw Amendment application to rezone the property from existing zone (RS-1) to a 2 storey triplex development. The project proposes 6 Bike storage and 5 off-street parking stalls.',
        {'units_total': 3, 'number_of_stories': 2, 'parking_vehicle_stalls': 5, 'parking_bike_stalls': 6},
        None,
    ),
    (
        '648 West 14th Street',
        'Inspired Architecture has submitted a Rezoning Application to the City of North Vancouver for 648 W 14th Street to allow for the development of a triplex with 3 Accessory Dwelling Units, 11 bicycle parking spaces and 5 parking spaces. Your comments will be shared with City staff and the Applicant to help shape the proposal through the review process. Comments will not be posted publicly or shared with Council. For information on how to provide feedback during the Council process or Public Hearing, visit cnv.org/PublicHearings . Which of the following best describes you: I live in the City of North Vancouver I work in the City of North Vancouver I live AND work in the City of North Vancouver None of the above Please provide your contact info if you would like a response to your feedback. Your contact info will be shared with the Applicant but not with Council. The City is collecting your personal information in accordance with Section 26(c) of the Freedom of Information and Protection of Privacy Act. The City collects your information for the purposes of administering City programs and services, including permits and licensing services. If you have any questions, please contact the Privacy Coordinator at 141 West 14th Street, North Vancouver, BC V7M 1H9 or planning@cnv.org or 604-985-7761.',
        {'units_total': 3, 'parking_vehicle_stalls': 5, 'parking_bike_stalls': 11, 'development_class': 'residential'},
        None,
    ),
    (
        '2416 Western Avenue',
        'Architectural Collective Inc. has applied for a Zoning Bylaw Amendment application to rezone the property from Existing Zone (RS1) to a new Zone CD zone to allow three residential buildings of two and three storeys, with a total of eighteen (18) units at a density of 1 FSR. The proposal includes lane dedication along the north side of the lot connecting the rear lane to Western Avenue, fourteen (14) vehicle parking stalls, a parking variance for 5 parking stalls, transportation demand measurements and the provision of twenty-nine (29) secure bicycle parking stalls.',
        {'units_total': 18, 'number_of_stories': 3, 'parking_vehicle_stalls': 14, 'parking_bike_stalls': 29, 'development_class': 'residential'},
        None,
    ),
    (
        '275 East 2nd Street',
        'Rezoning from RM-1 to a CD zone to develop a five storey residential rental building. Barry Savage Three Shores Development Telephone: 604-505-8818 Email: bsavage@threeshoresdevelopment.com',
        {'number_of_stories': 5, 'development_class': 'residential'},
        None,
    ),
    (
        '642 East 6th Street',
        'Vela Design Bld, has applied for a Zoning Bylaw Amendment application to rezone the property from RS1 Zone to RS2 Zone to allow for a 2 storey plus basement residential building.',
        {'number_of_stories': 2, 'development_class': 'residential'},
        None,
    ),
    (
        '880 West 15th Street',
        'Rezoning to allow a mixed use building with 41 residential rental units and three commercial units. Michael Cox Gateway Architecture Telephone: 604-608-1868 Email: mike@designvancouver.com',
        {'units_total': 41, 'development_class': 'mixed'},
        {'commercial': 3},
    ),
    (
        '2612 Lonsdale Avenue',
        'The City has received a rezoning application for 2612 Lonsdale Avenue to allow for the construction of a five-storey rental apartment building with 23 units.',
        {'units_total': 23, 'number_of_stories': 5, 'development_class': 'residential'},
        None,
    ),
    (
        '800 Marine Drive',
        'Cascadia Green has applied to rezone the site to permit a 4-storey mixed-use building with approximately 22,200 square feet of commercial space on the first two levels and 14,200 square feet of residential units above. Emma Chow Planning Lead Telephone: 604-983-7357 Email: planning@cnv.org Maryam Lotfi Cascadia Green Development Company Telephone: 604-771-6534 Email: maryam@cascadiagreendev.com',
        {'number_of_stories': 4, 'development_class': 'mixed'},
        None,
    ),
    (
        '818-858 West 15th Street',
        'Polygon Development 237 Ltd. has applied to rezone the property to allow for a six-storey mixed-use building with approximately 11,000 sq. ft. of ground floor retail and 90 strata units. Jacqueline Garvin Polygon Development 237 Ltd. Telephone: 604-877-1131 Email: jgarvin@polyhomes.com',
        {'units_total': 90, 'number_of_stories': 6, 'development_class': 'mixed'},
        {'strata': 90},
    ),
    (
        '210-230 East 2nd Street',
        'The City of North Vancouver has received a development application for 210 & 230 East 2nd Street to rezone the parcels to permit 160 rental residential units with 120 underground vehicle parking spaces. The applicant is GWL Realty Advisors. Project approved by Council on September 16, 2019. David Johnson Planner ll Tel: 604 990 4219 Email: planning@cnv.org Michael Reed GWL Realty Advisors Tel: 604 713 8919 Email: michael.reed@gwlra.com',
        {'units_total': 160, 'parking_vehicle_stalls': 120, 'development_class': 'residential'},
        None,
    ),
    (
        '127-129 East 12th Street',
        'The City of north Vancouver has received an Zoning amendment application for 127-129 East 12 th Street Avenue to rezone the parcel from Medium Density Apartment Residential 1 (RM-1) to a Comprehensive Development Zone to permit the development of a two to six storey, 64 unit rental apartment building. Project approved by Council on October 1, 2018. The following table indicates upcoming public input opportunities and will be updated as dates and locations are determined: David Johnson Development Planner Tel: 604-990-4219 Email: planning@cnv.org Riad Yassin Domus Homes 918-1030 West Georgia Street Vancouver, BC Tel: 604-662-7900 Email: riad@domushomes.ca',
        {'units_total': 64, 'number_of_stories': 6, 'development_class': 'residential'},
        None,
    ),
    (
        '150 East 8th Street',
        'Adera Crest Projects Ltd. has submitted a Development Application to rezone 150 East 8 th Street to allow an infill development on a 1.44 acre portion of the lot to the west of the existing three story Telus building (which would remain). The proposal is to add 161 apartments and 17 townhome units (for a total of 178 units) in two buildings over two levels of underground parking. The proposed buildings would be six storeys from East 11th Street. The proposed development would have a density of 2.6 times the lot area (FSR) which is consistent with the “Residential Level Five” designation in the Official Community Plan. Project approved by Council on April 18, 2018. The following table indicates upcoming public input opportunities and will be updated as dates and locations are determined. Planning Department Phone: 604-983-7357 Email: planning@cnv.org Rocky Sethi, VP Development Crest Adera Projects Ltd. Tel: 604 684-8277 Email: rockys@adera.com',
        {'units_total': 178, 'number_of_stories': 6, 'development_class': 'residential', 'floor_area': 1.44, 'floor_area_unit': 'acre'},
        {'apartment': 161, 'townhouse': 17},
    ),
]

@pytest.mark.parametrize("label,prose,expected,mix", CASES)
def test_prose_fields(label, prose, expected, mix):
    out = features.extract_all(prose)
    for key, want in expected.items():
        assert out[key] == want, f"{label}: {key} expected {want!r}, got {out[key]!r}"
    if mix:
        got = out['unit_mix'] or {}
        for k, v in mix.items():
            assert got.get(k) == v, f"{label}: unit_mix[{k!r}] expected {v!r}, got {got.get(k)!r}"
