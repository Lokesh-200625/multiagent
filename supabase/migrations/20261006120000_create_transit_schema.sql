-- ============================================================================
-- PilgrimAI - Transit GTFS Storage Schema
-- Migration: 20261006120000
--
-- Scope:
--   C1-A: PostgreSQL storage for static GTFS transit data.
--
-- Design decisions:
--   - GTFS shapes are intentionally NOT stored.
--   - GTFS HH:MM:SS values are normalized to INTEGER seconds.
--   - Feed content is identified by SHA-256 for idempotent ingestion.
--   - Invalid rows are quarantined instead of silently discarded.
--   - No query-performance indexes are added yet.
--   - Generated transfers are stored separately from source GTFS data.
--   - Routing is NOT implemented by this migration.
-- ============================================================================


-- ============================================================================
-- 1. TRANSIT FEEDS
-- ============================================================================
-- One row represents one downloaded/validated GTFS feed version.
-- content_sha256 is the primary idempotency mechanism for re-ingestion.

CREATE TABLE IF NOT EXISTS public.transit_feeds (
    id uuid DEFAULT gen_random_uuid() NOT NULL,

    provider_id text NOT NULL,
    provider_name text NOT NULL,

    source_url text,

    -- GTFS feed_info metadata
    publisher_name text,
    publisher_url text,
    feed_lang text,
    default_lang text,
    contact_email text,
    contact_url text,

    feed_version text,

    valid_from date,
    valid_until date,

    content_sha256 text NOT NULL,

    fetched_at timestamptz DEFAULT now() NOT NULL,

    -- Number of rows loaded per GTFS file.
    row_counts jsonb DEFAULT '{}'::jsonb NOT NULL,

    -- C0 audit summary retained with the feed.
    audit_summary jsonb DEFAULT '{}'::jsonb NOT NULL,

    status text DEFAULT 'STAGING'::text NOT NULL,

    activated_at timestamptz,

    created_at timestamptz DEFAULT now() NOT NULL,

    CONSTRAINT transit_feeds_pkey
        PRIMARY KEY (id),

    CONSTRAINT transit_feeds_status_check
        CHECK (
            status = ANY (
                ARRAY[
                    'STAGING'::text,
                    'LOADED'::text,
                    'ACTIVE'::text,
                    'FAILED'::text,
                    'QUARANTINED'::text
                ]
            )
        ),

    CONSTRAINT transit_feeds_hash_check
        CHECK (
            content_sha256 ~ '^[0-9a-fA-F]{64}$'
        ),

    CONSTRAINT transit_feeds_date_range_check
        CHECK (
            valid_from IS NULL
            OR valid_until IS NULL
            OR valid_until >= valid_from
        )
);


ALTER TABLE public.transit_feeds OWNER TO postgres;


ALTER TABLE ONLY public.transit_feeds
    ADD CONSTRAINT transit_feeds_provider_hash_key
    UNIQUE (provider_id, content_sha256);


-- Only one feed version can be ACTIVE for a provider at a time.
CREATE UNIQUE INDEX transit_feeds_one_active_per_provider
    ON public.transit_feeds (provider_id)
    WHERE status = 'ACTIVE';


-- ============================================================================
-- 2. AGENCIES
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transit_agencies (
    feed_id uuid NOT NULL,

    agency_id text NOT NULL,
    agency_name text NOT NULL,
    agency_url text,
    agency_timezone text,
    agency_lang text,
    agency_phone text,
    agency_fare_url text,

    CONSTRAINT transit_agencies_pkey
        PRIMARY KEY (feed_id, agency_id),

    CONSTRAINT transit_agencies_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE
);


ALTER TABLE public.transit_agencies OWNER TO postgres;


-- ============================================================================
-- 3. SERVICE CALENDAR
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transit_calendar (
    feed_id uuid NOT NULL,

    service_id text NOT NULL,

    monday boolean NOT NULL DEFAULT false,
    tuesday boolean NOT NULL DEFAULT false,
    wednesday boolean NOT NULL DEFAULT false,
    thursday boolean NOT NULL DEFAULT false,
    friday boolean NOT NULL DEFAULT false,
    saturday boolean NOT NULL DEFAULT false,
    sunday boolean NOT NULL DEFAULT false,

    start_date date NOT NULL,
    end_date date NOT NULL,

    CONSTRAINT transit_calendar_pkey
        PRIMARY KEY (feed_id, service_id),

    CONSTRAINT transit_calendar_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE,

    CONSTRAINT transit_calendar_date_range_check
        CHECK (end_date >= start_date)
);


ALTER TABLE public.transit_calendar OWNER TO postgres;


-- ============================================================================
-- 4. STOPS
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transit_stops (
    feed_id uuid NOT NULL,

    stop_id text NOT NULL,

    stop_code text,
    stop_name text NOT NULL,
    stop_desc text,

    stop_lat double precision,
    stop_lon double precision,

    zone_id text,
    stop_url text,

    location_type smallint,
    parent_station text,

    wheelchair_boarding smallint,

    CONSTRAINT transit_stops_pkey
        PRIMARY KEY (feed_id, stop_id),

    CONSTRAINT transit_stops_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE,

    CONSTRAINT transit_stops_lat_check
        CHECK (
            stop_lat IS NULL
            OR stop_lat BETWEEN -90.0 AND 90.0
        ),

    CONSTRAINT transit_stops_lon_check
        CHECK (
            stop_lon IS NULL
            OR stop_lon BETWEEN -180.0 AND 180.0
        ),

    CONSTRAINT transit_stops_location_type_check
        CHECK (
            location_type IS NULL
            OR location_type BETWEEN 0 AND 4
        ),

    CONSTRAINT transit_stops_wheelchair_check
        CHECK (
            wheelchair_boarding IS NULL
            OR wheelchair_boarding BETWEEN 0 AND 2
        )
);


ALTER TABLE public.transit_stops OWNER TO postgres;


-- ============================================================================
-- 5. ROUTES
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transit_routes (
    feed_id uuid NOT NULL,

    route_id text NOT NULL,

    agency_id text,

    route_short_name text,
    route_long_name text,
    route_desc text,

    route_type integer NOT NULL,

    -- Normalized application-level mode:
    -- bus / metro / rail / tram / ferry / unknown
    mode text NOT NULL,

    route_url text,
    route_color text,
    route_text_color text,

    CONSTRAINT transit_routes_pkey
        PRIMARY KEY (feed_id, route_id),

    CONSTRAINT transit_routes_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE,

    CONSTRAINT transit_routes_mode_check
        CHECK (
            mode = ANY (
                ARRAY[
                    'bus'::text,
                    'metro'::text,
                    'rail'::text,
                    'tram'::text,
                    'ferry'::text,
                    'unknown'::text
                ]
            )
        )
);


ALTER TABLE public.transit_routes OWNER TO postgres;


-- ============================================================================
-- 6. TRIPS
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transit_trips (
    feed_id uuid NOT NULL,

    trip_id text NOT NULL,

    route_id text NOT NULL,
    service_id text NOT NULL,

    trip_headsign text,
    trip_short_name text,

    direction_id smallint,

    block_id text,
    shape_id text,

    wheelchair_accessible smallint,
    bikes_allowed smallint,

    CONSTRAINT transit_trips_pkey
        PRIMARY KEY (feed_id, trip_id),

    CONSTRAINT transit_trips_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE
);


ALTER TABLE public.transit_trips OWNER TO postgres;


-- ============================================================================
-- 7. STOP TIMES
-- ============================================================================
-- IMPORTANT:
-- arrival_seconds / departure_seconds are INTEGER seconds from service-day
-- midnight.
--
-- Values >= 86400 are valid GTFS extended times.
-- Example:
--   24:10:00 -> 87000
--   45:23:21 -> 163? seconds
--
-- We intentionally do NOT impose a 24-hour upper bound.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transit_stop_times (
    feed_id uuid NOT NULL,

    trip_id text NOT NULL,
    stop_sequence integer NOT NULL,
    stop_id text NOT NULL,

    arrival_seconds integer,
    departure_seconds integer,

    stop_headsign text,

    pickup_type smallint,
    drop_off_type smallint,

    continuous_pickup smallint,
    continuous_drop_off smallint,

    timepoint smallint,

    CONSTRAINT transit_stop_times_pkey
        PRIMARY KEY (feed_id, trip_id, stop_sequence),

    CONSTRAINT transit_stop_times_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE,

    CONSTRAINT transit_stop_times_sequence_check
        CHECK (stop_sequence >= 0),

    CONSTRAINT transit_stop_times_arrival_check
        CHECK (
            arrival_seconds IS NULL
            OR arrival_seconds >= 0
        ),

    CONSTRAINT transit_stop_times_departure_check
        CHECK (
            departure_seconds IS NULL
            OR departure_seconds >= 0
        )
);


ALTER TABLE public.transit_stop_times OWNER TO postgres;


-- ============================================================================
-- 8. FARES
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transit_fares (
    feed_id uuid NOT NULL,

    fare_id text NOT NULL,

    price numeric(10,2) NOT NULL,
    currency_type char(3) NOT NULL,

    payment_method smallint,

    transfers smallint,
    agency_id text,

    transfer_duration integer,

    CONSTRAINT transit_fares_pkey
        PRIMARY KEY (feed_id, fare_id),

    CONSTRAINT transit_fares_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE,

    CONSTRAINT transit_fares_price_check
        CHECK (price >= 0),

    CONSTRAINT transit_fares_payment_method_check
        CHECK (
            payment_method IS NULL
            OR payment_method BETWEEN 0 AND 1
        ),

    CONSTRAINT transit_fares_transfers_check
        CHECK (
            transfers IS NULL
            OR transfers >= 0
        ),

    CONSTRAINT transit_fares_transfer_duration_check
        CHECK (
            transfer_duration IS NULL
            OR transfer_duration >= 0
        )
);


ALTER TABLE public.transit_fares OWNER TO postgres;


-- ============================================================================
-- 9. FARE RULES
-- ============================================================================
-- GTFS fare rules contain nullable dimensions, so a surrogate identity key
-- is used rather than making nullable GTFS columns part of the primary key.
-- Duplicate semantic rules are prevented with a unique expression index.

CREATE TABLE IF NOT EXISTS public.transit_fare_rules (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,

    feed_id uuid NOT NULL,

    fare_id text NOT NULL,

    route_id text,
    origin_id text,
    destination_id text,
    contains_id text,

    CONSTRAINT transit_fare_rules_pkey
        PRIMARY KEY (id),

    CONSTRAINT transit_fare_rules_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE
);


ALTER TABLE public.transit_fare_rules OWNER TO postgres;


CREATE UNIQUE INDEX transit_fare_rules_unique_rule
    ON public.transit_fare_rules (
        feed_id,
        fare_id,
        COALESCE(route_id, ''),
        COALESCE(origin_id, ''),
        COALESCE(destination_id, ''),
        COALESCE(contains_id, '')
    );


-- ============================================================================
-- 10. GENERATED TRANSFERS
-- ============================================================================
-- These are NOT copied from GTFS.
--
-- They are generated later by C5 from:
--   1. parent_station relationships
--   2. nearby stops within the configured walking threshold
--
-- transfer_source documents how the edge was generated.

CREATE TABLE IF NOT EXISTS public.transit_transfers (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,

    feed_id uuid NOT NULL,

    from_stop_id text NOT NULL,
    to_stop_id text NOT NULL,

    distance_m integer NOT NULL,
    walk_seconds integer NOT NULL,

    transfer_source text NOT NULL,

    created_at timestamptz DEFAULT now() NOT NULL,

    CONSTRAINT transit_transfers_pkey
        PRIMARY KEY (id),

    CONSTRAINT transit_transfers_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE,

    CONSTRAINT transit_transfers_distance_check
        CHECK (distance_m >= 0),

    CONSTRAINT transit_transfers_walk_seconds_check
        CHECK (walk_seconds >= 0),

    CONSTRAINT transit_transfers_source_check
        CHECK (
            transfer_source = ANY (
                ARRAY[
                    'parent_station'::text,
                    'nearby_stop'::text
                ]
            )
        )
);


ALTER TABLE public.transit_transfers OWNER TO postgres;


CREATE UNIQUE INDEX transit_transfers_unique_edge
    ON public.transit_transfers (
        feed_id,
        from_stop_id,
        to_stop_id
    );


-- ============================================================================
-- 11. QUARANTINE
-- ============================================================================
-- Invalid rows are retained for debugging/auditability rather than silently
-- discarded.
--
-- raw_row stores the original GTFS CSV row as JSONB.

CREATE TABLE IF NOT EXISTS public.transit_quarantine (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,

    feed_id uuid NOT NULL,

    table_name text NOT NULL,
    row_number bigint NOT NULL,

    reason_code text NOT NULL,
    reason_detail text,

    raw_row jsonb NOT NULL,

    created_at timestamptz DEFAULT now() NOT NULL,

    CONSTRAINT transit_quarantine_pkey
        PRIMARY KEY (id),

    CONSTRAINT transit_quarantine_feed_fkey
        FOREIGN KEY (feed_id)
        REFERENCES public.transit_feeds(id)
        ON DELETE CASCADE,

    CONSTRAINT transit_quarantine_row_number_check
        CHECK (row_number > 0)
);


ALTER TABLE public.transit_quarantine OWNER TO postgres;


-- ============================================================================
-- 12. ROW LEVEL SECURITY
-- ============================================================================
-- Transit data is static provider data, not user-owned data.
--
-- For now:
--   - RLS is enabled.
--   - No public/authenticated policies are created yet.
--   - Backend ingestion/query operations will use the server-side database
--     connection.
--
-- Public/API exposure can be explicitly added later once the query layer is
-- finalized.
-- ============================================================================

ALTER TABLE public.transit_feeds ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_agencies ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_calendar ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_stops ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_routes ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_trips ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_stop_times ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_fares ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_fare_rules ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_transfers ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.transit_quarantine ENABLE ROW LEVEL SECURITY;


-- ============================================================================
-- 13. TABLE PRIVILEGES
-- ============================================================================
-- Match the existing Supabase project convention while RLS remains the
-- authorization boundary.
-- ============================================================================

GRANT ALL ON TABLE public.transit_feeds
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_agencies
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_calendar
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_stops
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_routes
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_trips
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_stop_times
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_fares
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_fare_rules
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_transfers
    TO anon, authenticated, service_role;

GRANT ALL ON TABLE public.transit_quarantine
    TO anon, authenticated, service_role;


-- -- Identity sequences need privileges for service-side inserts.
-- GRANT ALL ON SEQUENCE public.transit_fare_rules_id_seq
--     TO postgres, anon, authenticated, service_role;

-- GRANT ALL ON SEQUENCE public.transit_transfers_id_seq
--     TO postgres, anon, authenticated, service_role;

-- GRANT ALL ON SEQUENCE public.transit_quarantine_id_seq
--     TO postgres, anon, authenticated, service_role;


-- ============================================================================
-- END
-- ============================================================================