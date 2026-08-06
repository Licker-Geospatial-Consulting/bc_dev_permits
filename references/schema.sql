-- ============================================================================
-- Development Permit Scraper — target schema (PostgreSQL)
-- One normalized model for commercial, residential, mixed-use, industrial,
-- and institutional applications across all municipalities.
-- ============================================================================

-- Optional: keep municipalities in their own table so COUNCIL.md maps 1:1 to rows.
CREATE TABLE municipality (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,        -- 'City of North Vancouver'
    slug          TEXT NOT NULL UNIQUE,        -- 'north_van'
    source_type   TEXT NOT NULL,               -- 'html' | 'arcgis' | 'vertigis'
    list_url      TEXT,
    notes         TEXT
);

-- ============================================================================
-- dev_permit — one row per application. This is the deliverable table.
-- ============================================================================
CREATE TABLE dev_permit (
    id                     BIGSERIAL PRIMARY KEY,

    -- identity / provenance -------------------------------------------------
    municipality           TEXT NOT NULL,          -- FK-ish to municipality.slug
    permit_id              TEXT,                   -- application/permit number, e.g. PLN2025-00010, 22-039
    source_url             TEXT NOT NULL,          -- the exact page/feature URL scraped
    development_name       TEXT,                   -- 'The Trails Future Phases', project title if any

    -- CORE fields (requested) ----------------------------------------------
    address                TEXT,
    latitude               DOUBLE PRECISION,       -- "lot lat"
    longitude              DOUBLE PRECISION,       -- "lot lon"
    permit_type            TEXT,                   -- 'Rezoning','Development Permit','DVP','Subdivision','Building Permit'...
    development_class      TEXT,                   -- 'residential'|'commercial'|'mixed'|'industrial'|'institutional'|'unknown'
    status                 TEXT,                   -- 'Application Accepted','Under Review','Approved',...
    floor_area             NUMERIC,                -- gross floor area (m^2); keep unit consistent, see floor_area_unit
    floor_area_unit        TEXT DEFAULT 'm2',
    footprint_area         NUMERIC,                -- building footprint (m^2)
    number_of_stories      INTEGER,
    units_total            INTEGER,                -- total dwelling/unit count
    unit_mix               JSONB,                  -- {"1-bed":19,"2-bed":12,"3-bed":3,"suite":6}  ("count of unit types")
    rental_or_strata       TEXT,                   -- 'rental'|'strata'|'mixed'|'unknown'
    rental_subtype         TEXT,                   -- 'purpose-built rental','mid-market rental','market rental',...
    rental_mix             JSONB,                  -- affordability split, {"market-rental":49,"mid-market-rental":6}
    parking_vehicle_stalls INTEGER,
    parking_bike_stalls    INTEGER,
    parking_notes          TEXT,

    -- OPTIONAL fields (requested) ------------------------------------------
    building_materials     TEXT,
    retrofit_info          TEXT,                   -- new build vs retrofit/renovation details
    zoning_density         TEXT,                   -- FSR/FAR, density, e.g. 'FSR 2.6'
    energy_info            TEXT,                   -- BC Energy Step Code level, LEED, Passive House, etc.
    mechanical_system_info TEXT,                   -- HVAC / heat pump / district energy notes

    -- extraction bookkeeping ------------------------------------------------
    raw_text               TEXT,                    -- the prose/attributes the fields were parsed from (re-extract later)
    is_parsed              BOOLEAN DEFAULT FALSE,   -- true once fields came from text
    needs_pdf_extraction   BOOLEAN DEFAULT FALSE,   -- true when only PDFs are available
    needs_review           BOOLEAN DEFAULT FALSE,   -- true when confidence < 0.6
    extraction_method      TEXT,                    -- 'html' | 'arcgis' | 'ollama_text' | 'ollama_pdf' | 'manual'
    extraction_confidence  NUMERIC,
    checksum               TEXT,                   -- hash of source text/attributes for change detection
    first_seen             TIMESTAMPTZ DEFAULT now(),
    last_updated           TIMESTAMPTZ DEFAULT now(),

    UNIQUE (municipality, permit_id)
);

-- ============================================================================
-- occupancy / building type — WHY a child table instead of columns:
-- the user noted "some buildings have many". A mixed-use tower is commercial AND
-- residential (and sometimes institutional). Sparse columns (is_commercial,
-- is_residential, ...) get unwieldy and can't hold per-type detail like the floor
-- area of each use. A child table stays clean and lets one permit carry N uses.
-- (If you prefer zero joins, dev_permit.development_class + a JSONB occupancy_types
--  array on dev_permit is an acceptable denormalized alternative.)
-- ============================================================================
CREATE TABLE dev_permit_occupancy (
    id          BIGSERIAL PRIMARY KEY,
    permit_id   BIGINT NOT NULL REFERENCES dev_permit(id) ON DELETE CASCADE,
    occupancy       TEXT NOT NULL, -- 'residential','retail','office','industrial','institutional','hotel',...
    detail          TEXT,          -- free text, e.g. 'ground-floor retail','commercial and office space'
    floor_area      NUMERIC,       -- area attributable to this use, if stated
    floor_area_unit TEXT DEFAULT 'm2'
);

-- ============================================================================
-- dates — applications have many milestones and they vary by city; a child
-- table beats a fixed set of date columns. Common ones still queryable via a view.
-- ============================================================================
CREATE TABLE dev_permit_milestone (
    id             BIGSERIAL PRIMARY KEY,
    permit_id      BIGINT NOT NULL REFERENCES dev_permit(id) ON DELETE CASCADE,
    milestone      TEXT NOT NULL,    -- 'Application Accepted','Public Hearing','Council','Decision',...
    milestone_date DATE
);

-- ============================================================================
-- documents — PDF/attachment links. Populated on the PDF-fallback path AND
-- whenever a page links supporting docs (drawings, staff reports).
-- ============================================================================
CREATE TABLE dev_document (
    id          BIGSERIAL PRIMARY KEY,
    permit_id   BIGINT REFERENCES dev_permit(id) ON DELETE CASCADE,
    municipality TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT,              -- 'Architectural Drawings','Staff Report',...
    doc_role    TEXT,              -- 'drawings','staff_report','notice','application','other'
    checksum    TEXT,
    downloaded  BOOLEAN DEFAULT FALSE,
    local_path  TEXT,
    extracted   BOOLEAN DEFAULT FALSE,
    UNIQUE (municipality, url)
);

-- Convenience view: flatten the most-used milestone dates back onto the permit.
CREATE VIEW dev_permit_flat AS
SELECT p.*,
       (SELECT milestone_date FROM dev_permit_milestone m
         WHERE m.permit_id = p.id AND m.milestone ILIKE 'application%' LIMIT 1) AS received_date,
       (SELECT milestone_date FROM dev_permit_milestone m
         WHERE m.permit_id = p.id AND m.milestone ILIKE 'council%'     LIMIT 1) AS council_date,
       (SELECT milestone_date FROM dev_permit_milestone m
         WHERE m.permit_id = p.id AND m.milestone ILIKE 'decision%'    LIMIT 1) AS decision_date
FROM dev_permit p;
