-- ============================================================
-- Smart Traffic Survey Deployment Assistant
-- PostgreSQL / PostGIS initialisation script
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- -----------------------------------------------------------
-- Main table
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS survey_history (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    geom            geometry(Point, 4326) NOT NULL,
    azimuth         FLOAT           CHECK (azimuth IS NULL OR (azimuth >= 0 AND azimuth < 360)),
    equipment_type  VARCHAR(20)     NOT NULL CHECK (equipment_type IN ('node', 'zone', 'camera_dir', 'camera_omni')),
    layout_family   VARCHAR(40)     NOT NULL DEFAULT 'unknown',
    source_group    VARCHAR(255)    NOT NULL DEFAULT '',
    parser_version  VARCHAR(80)     NOT NULL DEFAULT 'legacy',
    import_batch_id UUID,
    raw_upload_id   UUID,
    site_name       VARCHAR(255)    NOT NULL DEFAULT '',
    site_source_group VARCHAR(255)  NOT NULL DEFAULT '',
    site_lat        FLOAT,
    site_lng        FLOAT,
    site_distance_m FLOAT,
    required_camera_count INTEGER CHECK (
        required_camera_count IS NULL OR required_camera_count > 0
    ),
    camera_count_source VARCHAR(40) NOT NULL DEFAULT '',
    timestamp       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    usage_count     INTEGER         NOT NULL DEFAULT 1 CHECK (usage_count > 0),
    source_file     VARCHAR(255),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS layout_family VARCHAR(40) NOT NULL DEFAULT 'unknown';

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS source_group VARCHAR(255) NOT NULL DEFAULT '';

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS parser_version VARCHAR(80) NOT NULL DEFAULT 'legacy';

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS import_batch_id UUID;

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS raw_upload_id UUID;

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS site_name VARCHAR(255) NOT NULL DEFAULT '';

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS site_source_group VARCHAR(255) NOT NULL DEFAULT '';

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS site_lat FLOAT;

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS site_lng FLOAT;

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS site_distance_m FLOAT;

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS required_camera_count INTEGER CHECK (
        required_camera_count IS NULL OR required_camera_count > 0
    );

ALTER TABLE survey_history
    ADD COLUMN IF NOT EXISTS camera_count_source VARCHAR(40) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS raw_uploads (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_file     VARCHAR(255)    NOT NULL,
    content_hash    CHAR(64)        NOT NULL UNIQUE,
    content         BYTEA           NOT NULL,
    content_type    VARCHAR(120)    NOT NULL DEFAULT 'application/vnd.google-earth.kml+xml',
    uploaded_by     VARCHAR(255)    NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_upload_layer_reviews (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_upload_id   UUID        NOT NULL REFERENCES raw_uploads(id) ON DELETE CASCADE,
    layer_path      TEXT        NOT NULL,
    review_status   VARCHAR(20) NOT NULL CHECK (
        review_status IN ('selected', 'excluded')
    ),
    selected_family VARCHAR(20) CHECK (
        selected_family IS NULL OR selected_family IN (
            'ic', 'od', 'ipod', 'hik', 'atc', 'site', 'unknown'
        )
    ),
    review_tags     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    reviewed_by     VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (raw_upload_id, layer_path),
    CHECK (
        (review_status = 'selected' AND selected_family IS NOT NULL)
        OR (review_status = 'excluded' AND selected_family IS NULL)
    )
);

ALTER TABLE raw_upload_layer_reviews
    DROP CONSTRAINT IF EXISTS raw_upload_layer_reviews_selected_family_check;
ALTER TABLE raw_upload_layer_reviews
    ADD CONSTRAINT raw_upload_layer_reviews_selected_family_check CHECK (
        selected_family IS NULL OR selected_family IN (
            'ic', 'od', 'ipod', 'hik', 'atc', 'site', 'unknown'
        )
    );

ALTER TABLE raw_upload_layer_reviews
    ADD COLUMN IF NOT EXISTS review_tags JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS raw_placemarks (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_upload_id       UUID        NOT NULL REFERENCES raw_uploads(id) ON DELETE CASCADE,
    placemark_index     INTEGER     NOT NULL CHECK (placemark_index >= 0),
    folder_path         TEXT[]      NOT NULL DEFAULT '{}',
    placemark_name      TEXT        NOT NULL DEFAULT '',
    description         TEXT        NOT NULL DEFAULT '',
    style_url           TEXT        NOT NULL DEFAULT '',
    style_id            TEXT        NOT NULL DEFAULT '',
    style_definition    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    extended_data       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    geometry_types      TEXT[]      NOT NULL DEFAULT '{}',
    geometry            JSONB       NOT NULL DEFAULT '[]'::jsonb,
    raw_coordinates     TEXT[]      NOT NULL DEFAULT '{}',
    raw_kml_fragment    TEXT        NOT NULL,
    raw_parser_version  VARCHAR(80) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (raw_upload_id, placemark_index, raw_parser_version)
);

CREATE TABLE IF NOT EXISTS classification_decisions (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_placemark_id            UUID        NOT NULL REFERENCES raw_placemarks(id) ON DELETE CASCADE,
    business_role               VARCHAR(40) NOT NULL CHECK (business_role IN (
        'site', 'camera_location', 'camera_direction',
        'atc_location', 'atc_tube_line', 'route',
        'reference_point', 'unknown', 'ignored_historical_polygon'
    )),
    task_family                 VARCHAR(20) NOT NULL CHECK (task_family IN (
        'ic', 'od', 'ipod', 'hik', 'atc', 'unknown', 'mixed'
    )),
    required_camera_count       INTEGER CHECK (
        required_camera_count IS NULL OR required_camera_count > 0
    ),
    camera_count_source         VARCHAR(40) NOT NULL DEFAULT '',
    classification_confidence   FLOAT       NOT NULL CHECK (
        classification_confidence >= 0 AND classification_confidence <= 1
    ),
    classification_reasons      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    classification_method       VARCHAR(40) NOT NULL DEFAULT 'rule_engine',
    classification_status       VARCHAR(40) NOT NULL DEFAULT 'suggested',
    model_version               VARCHAR(120) NOT NULL DEFAULT '',
    decision_metadata           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    rule_version                VARCHAR(80) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (raw_placemark_id, rule_version)
);

ALTER TABLE classification_decisions
    DROP CONSTRAINT IF EXISTS classification_decisions_business_role_check;
ALTER TABLE classification_decisions
    ADD CONSTRAINT classification_decisions_business_role_check CHECK (
        business_role IN (
            'site', 'camera_location', 'camera_direction',
            'atc_location', 'atc_tube_line', 'route',
            'reference_point', 'unknown', 'ignored_historical_polygon'
        )
    );
ALTER TABLE classification_decisions
    DROP CONSTRAINT IF EXISTS classification_decisions_task_family_check;
ALTER TABLE classification_decisions
    ADD CONSTRAINT classification_decisions_task_family_check CHECK (
        task_family IN ('ic', 'od', 'ipod', 'hik', 'atc', 'unknown', 'mixed')
    );

ALTER TABLE classification_decisions
    ADD COLUMN IF NOT EXISTS model_version VARCHAR(120) NOT NULL DEFAULT '';

ALTER TABLE classification_decisions
    ADD COLUMN IF NOT EXISTS decision_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS camera_observations (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_placemark_id            UUID        NOT NULL REFERENCES raw_placemarks(id) ON DELETE CASCADE,
    classification_decision_id  UUID        NOT NULL REFERENCES classification_decisions(id) ON DELETE CASCADE,
    placemark_name              TEXT        NOT NULL DEFAULT '',
    task_family                 VARCHAR(20) NOT NULL,
    source_position             JSONB       NOT NULL,
    geom                        geometry(Point, 4326) NOT NULL,
    location_method             VARCHAR(40) NOT NULL DEFAULT 'source_point' CHECK (
        location_method IN ('source_point', 'direction_point_2')
    ),
    classification_confidence   FLOAT       NOT NULL CHECK (
        classification_confidence >= 0 AND classification_confidence <= 1
    ),
    normalization_version       VARCHAR(100) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (classification_decision_id, normalization_version)
);

ALTER TABLE camera_observations
    ADD COLUMN IF NOT EXISTS location_method VARCHAR(40) NOT NULL DEFAULT 'source_point';
ALTER TABLE camera_observations
    DROP CONSTRAINT IF EXISTS camera_observations_location_method_check;
ALTER TABLE camera_observations
    ADD CONSTRAINT camera_observations_location_method_check CHECK (
        location_method IN ('source_point', 'direction_point_2')
    );

CREATE TABLE IF NOT EXISTS site_observations (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_placemark_id            UUID        NOT NULL REFERENCES raw_placemarks(id) ON DELETE CASCADE,
    classification_decision_id  UUID        NOT NULL REFERENCES classification_decisions(id) ON DELETE CASCADE,
    placemark_name              TEXT        NOT NULL DEFAULT '',
    task_family                 VARCHAR(20) NOT NULL,
    site_key                    VARCHAR(80),
    source_position             JSONB       NOT NULL,
    geom                        geometry(Point, 4326) NOT NULL,
    required_camera_count       INTEGER CHECK (
        required_camera_count IS NULL OR required_camera_count > 0
    ),
    camera_count_source         VARCHAR(40) NOT NULL DEFAULT '',
    classification_confidence   FLOAT       NOT NULL CHECK (
        classification_confidence >= 0 AND classification_confidence <= 1
    ),
    normalization_version       VARCHAR(100) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (classification_decision_id, normalization_version)
);

CREATE TABLE IF NOT EXISTS direction_line_observations (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_placemark_id            UUID        NOT NULL REFERENCES raw_placemarks(id) ON DELETE CASCADE,
    classification_decision_id  UUID        NOT NULL REFERENCES classification_decisions(id) ON DELETE CASCADE,
    placemark_name              TEXT        NOT NULL DEFAULT '',
    task_family                 VARCHAR(20) NOT NULL,
    source_positions            JSONB       NOT NULL,
    geom                        geometry(LineString, 4326) NOT NULL,
    point_count                 INTEGER     NOT NULL CHECK (point_count >= 2),
    camera_anchor_geom          geometry(Point, 4326),
    boundary_a_bearing          FLOAT CHECK (boundary_a_bearing >= 0 AND boundary_a_bearing < 360),
    boundary_b_bearing          FLOAT CHECK (boundary_b_bearing >= 0 AND boundary_b_bearing < 360),
    suggested_centre_bearing    FLOAT CHECK (suggested_centre_bearing >= 0 AND suggested_centre_bearing < 360),
    suggested_coverage_angle    FLOAT CHECK (suggested_coverage_angle >= 0 AND suggested_coverage_angle <= 180),
    geometry_status             VARCHAR(40) NOT NULL CHECK (geometry_status IN (
        'standard_three_point', 'nonstandard_review'
    )),
    binding_state               VARCHAR(40) NOT NULL CHECK (binding_state IN (
        'automatic', 'review_candidate', 'unbound', 'nonstandard_review'
    )),
    requires_review             BOOLEAN     NOT NULL DEFAULT FALSE,
    classification_confidence   FLOAT       NOT NULL CHECK (
        classification_confidence >= 0 AND classification_confidence <= 1
    ),
    normalization_version       VARCHAR(100) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (classification_decision_id, normalization_version)
);

CREATE TABLE IF NOT EXISTS camera_direction_bindings (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    camera_observation_id       UUID        NOT NULL REFERENCES camera_observations(id) ON DELETE CASCADE,
    direction_line_observation_id UUID      NOT NULL REFERENCES direction_line_observations(id) ON DELETE CASCADE,
    anchor_distance_m           FLOAT       NOT NULL CHECK (anchor_distance_m >= 0),
    binding_status              VARCHAR(40) NOT NULL CHECK (binding_status IN (
        'automatic', 'review_candidate', 'human_confirmed', 'human_rejected'
    )),
    is_selected                 BOOLEAN     NOT NULL DEFAULT FALSE,
    binding_confidence          FLOAT       NOT NULL CHECK (
        binding_confidence >= 0 AND binding_confidence <= 1
    ),
    binding_reasons             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    binding_version             VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (camera_observation_id, direction_line_observation_id, binding_version)
);

CREATE TABLE IF NOT EXISTS camera_site_relationships (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    camera_observation_id       UUID        NOT NULL REFERENCES camera_observations(id) ON DELETE CASCADE,
    site_observation_id         UUID        NOT NULL REFERENCES site_observations(id) ON DELETE CASCADE,
    relationship_role           VARCHAR(20) NOT NULL CHECK (relationship_role IN (
        'primary', 'fallback'
    )),
    distance_m                  FLOAT       NOT NULL CHECK (distance_m >= 0),
    association_method          VARCHAR(40) NOT NULL CHECK (association_method IN (
        'explicit_name_nearest', 'spatial_nearest'
    )),
    is_named_association        BOOLEAN     NOT NULL DEFAULT FALSE,
    relationship_confidence     FLOAT       NOT NULL CHECK (
        relationship_confidence >= 0 AND relationship_confidence <= 1
    ),
    relationship_reasons        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    relationship_status         VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        relationship_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    association_version         VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (camera_observation_id, site_observation_id, association_version)
);

CREATE TABLE IF NOT EXISTS camera_site_review_items (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    camera_observation_id       UUID        NOT NULL REFERENCES camera_observations(id) ON DELETE CASCADE,
    candidate_site_observation_id UUID      REFERENCES site_observations(id) ON DELETE SET NULL,
    review_code                 VARCHAR(60) NOT NULL CHECK (review_code IN (
        'unmatched_explicit_site_reference', 'no_site_candidates',
        'no_compatible_site', 'nearest_site_outside_radius',
        'layer_without_site_context'
    )),
    candidate_distance_m        FLOAT CHECK (
        candidate_distance_m IS NULL OR candidate_distance_m >= 0
    ),
    named_site_references       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    review_reasons              JSONB       NOT NULL DEFAULT '[]'::jsonb,
    review_status               VARCHAR(40) NOT NULL DEFAULT 'open' CHECK (
        review_status IN ('open', 'human_resolved', 'human_dismissed')
    ),
    association_version         VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (camera_observation_id, review_code, association_version)
);

ALTER TABLE camera_site_review_items
    DROP CONSTRAINT IF EXISTS camera_site_review_items_review_code_check;
ALTER TABLE camera_site_review_items
    ADD CONSTRAINT camera_site_review_items_review_code_check CHECK (
        review_code IN (
            'unmatched_explicit_site_reference', 'no_site_candidates',
            'no_compatible_site', 'nearest_site_outside_radius',
            'layer_without_site_context'
        )
    );

CREATE TABLE IF NOT EXISTS camera_location_clusters (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    cluster_key                 CHAR(64)    NOT NULL,
    representative_camera_observation_id UUID NOT NULL REFERENCES camera_observations(id),
    representative_geom         geometry(Point, 4326) NOT NULL,
    member_count                INTEGER     NOT NULL CHECK (member_count > 0),
    diameter_m                  FLOAT       NOT NULL CHECK (diameter_m >= 0),
    clustering_method           VARCHAR(40) NOT NULL DEFAULT 'complete_link',
    cluster_status              VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        cluster_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    cluster_version             VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (cluster_key, cluster_version)
);

CREATE TABLE IF NOT EXISTS camera_location_cluster_members (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    camera_location_cluster_id  UUID        NOT NULL REFERENCES camera_location_clusters(id) ON DELETE CASCADE,
    camera_observation_id       UUID        NOT NULL REFERENCES camera_observations(id) ON DELETE CASCADE,
    distance_to_representative_m FLOAT      NOT NULL CHECK (distance_to_representative_m >= 0),
    is_representative           BOOLEAN     NOT NULL DEFAULT FALSE,
    cluster_version             VARCHAR(100) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (camera_observation_id, cluster_version)
);

CREATE TABLE IF NOT EXISTS site_camera_count_decisions (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_observation_id         UUID        NOT NULL REFERENCES site_observations(id) ON DELETE CASCADE,
    required_camera_count       INTEGER CHECK (
        required_camera_count IS NULL OR required_camera_count > 0
    ),
    camera_count_source         VARCHAR(40) NOT NULL CHECK (camera_count_source IN (
        'explicit_description', 'explicit_description_conflict_primary',
        'historical_mode', 'insufficient_history'
    )),
    decision_method             VARCHAR(40) NOT NULL,
    decision_confidence         FLOAT       NOT NULL CHECK (
        decision_confidence >= 0 AND decision_confidence <= 1
    ),
    decision_reasons            JSONB       NOT NULL DEFAULT '[]'::jsonb,
    supporting_site_observation_ids JSONB   NOT NULL DEFAULT '[]'::jsonb,
    historical_count_votes      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    requires_review             BOOLEAN     NOT NULL DEFAULT FALSE,
    decision_status             VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        decision_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    decision_version            VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_observation_id, decision_version)
);

CREATE TABLE IF NOT EXISTS site_camera_generation_results (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_observation_id         UUID        NOT NULL REFERENCES site_observations(id) ON DELETE CASCADE,
    camera_count_decision_id    UUID        NOT NULL REFERENCES site_camera_count_decisions(id) ON DELETE CASCADE,
    required_camera_count       INTEGER CHECK (
        required_camera_count IS NULL OR required_camera_count > 0
    ),
    selected_camera_count       INTEGER     NOT NULL CHECK (selected_camera_count >= 0),
    missing_reliable_location_count INTEGER CHECK (
        missing_reliable_location_count IS NULL OR missing_reliable_location_count >= 0
    ),
    generation_status           VARCHAR(40) NOT NULL CHECK (generation_status IN (
        'complete', 'partial', 'requires_review'
    )),
    site_description_note       TEXT        NOT NULL DEFAULT '',
    generation_reasons          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    requires_review             BOOLEAN     NOT NULL DEFAULT FALSE,
    result_status               VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        result_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    generation_version          VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_observation_id, generation_version)
);

CREATE TABLE IF NOT EXISTS site_camera_generation_selections (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    generation_result_id        UUID        NOT NULL REFERENCES site_camera_generation_results(id) ON DELETE CASCADE,
    camera_location_cluster_id  UUID        NOT NULL REFERENCES camera_location_clusters(id) ON DELETE CASCADE,
    representative_camera_observation_id UUID NOT NULL REFERENCES camera_observations(id) ON DELETE CASCADE,
    source_relationship_id      UUID        REFERENCES camera_site_relationships(id) ON DELETE CASCADE,
    selection_tier              VARCHAR(40) NOT NULL CHECK (selection_tier IN (
        'primary', 'fallback', 'nearby_historical'
    )),
    selection_rank              INTEGER     NOT NULL CHECK (selection_rank > 0),
    distance_to_site_m          FLOAT       NOT NULL CHECK (distance_to_site_m >= 0),
    candidate_confidence        FLOAT       NOT NULL CHECK (
        candidate_confidence >= 0 AND candidate_confidence <= 1
    ),
    ranking_score               FLOAT       NOT NULL DEFAULT 0 CHECK (
        ranking_score >= 0 AND ranking_score <= 1
    ),
    ranking_method              VARCHAR(80) NOT NULL DEFAULT '',
    ranking_reasons             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    generation_version          VARCHAR(100) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (generation_result_id, selection_rank),
    UNIQUE (generation_result_id, camera_location_cluster_id)
);

ALTER TABLE site_camera_generation_selections
    ADD COLUMN IF NOT EXISTS ranking_score FLOAT NOT NULL DEFAULT 0 CHECK (
        ranking_score >= 0 AND ranking_score <= 1
    );
ALTER TABLE site_camera_generation_selections
    ADD COLUMN IF NOT EXISTS ranking_method VARCHAR(80) NOT NULL DEFAULT '';
ALTER TABLE site_camera_generation_selections
    ADD COLUMN IF NOT EXISTS ranking_reasons JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE site_camera_generation_results
    DROP CONSTRAINT IF EXISTS site_camera_generation_results_camera_count_decision_id_fkey;
ALTER TABLE site_camera_generation_results
    ADD CONSTRAINT site_camera_generation_results_camera_count_decision_id_fkey
    FOREIGN KEY (camera_count_decision_id)
    REFERENCES site_camera_count_decisions(id) ON DELETE CASCADE;

ALTER TABLE site_camera_generation_selections
    DROP CONSTRAINT IF EXISTS site_camera_generation_selectio_camera_location_cluster_id_fkey;
ALTER TABLE site_camera_generation_selections
    ADD CONSTRAINT site_camera_generation_selectio_camera_location_cluster_id_fkey
    FOREIGN KEY (camera_location_cluster_id)
    REFERENCES camera_location_clusters(id) ON DELETE CASCADE;

ALTER TABLE site_camera_generation_selections
    DROP CONSTRAINT IF EXISTS site_camera_generation_select_representative_camera_observ_fkey;
ALTER TABLE site_camera_generation_selections
    ADD CONSTRAINT site_camera_generation_select_representative_camera_observ_fkey
    FOREIGN KEY (representative_camera_observation_id)
    REFERENCES camera_observations(id) ON DELETE CASCADE;

ALTER TABLE site_camera_generation_selections
    DROP CONSTRAINT IF EXISTS site_camera_generation_selections_source_relationship_id_fkey;
ALTER TABLE site_camera_generation_selections
    ADD CONSTRAINT site_camera_generation_selections_source_relationship_id_fkey
    FOREIGN KEY (source_relationship_id)
    REFERENCES camera_site_relationships(id) ON DELETE CASCADE;

CREATE TABLE IF NOT EXISTS site_scope_polygon_observations (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_placemark_id            UUID        NOT NULL REFERENCES raw_placemarks(id) ON DELETE CASCADE,
    classification_decision_id  UUID        NOT NULL REFERENCES classification_decisions(id) ON DELETE CASCADE,
    placemark_name              TEXT        NOT NULL DEFAULT '',
    task_family                 VARCHAR(20) NOT NULL,
    outer_ring                  JSONB       NOT NULL,
    inner_rings                 JSONB       NOT NULL DEFAULT '[]'::jsonb,
    geom                        geometry(Polygon, 4326),
    distinct_outer_vertex_count INTEGER     NOT NULL CHECK (distinct_outer_vertex_count >= 0),
    style_evidence              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    scope_status                VARCHAR(40) NOT NULL CHECK (scope_status IN (
        'accepted_site_scope', 'unbound_review', 'invalid_geometry_review'
    )),
    requires_review             BOOLEAN     NOT NULL DEFAULT FALSE,
    classification_reasons      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    decision_status             VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        decision_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    scope_version               VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (classification_decision_id, scope_version)
);

CREATE TABLE IF NOT EXISTS site_scope_polygon_memberships (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    polygon_observation_id      UUID        NOT NULL REFERENCES site_scope_polygon_observations(id) ON DELETE CASCADE,
    site_observation_id         UUID        NOT NULL REFERENCES site_observations(id) ON DELETE CASCADE,
    membership_method           VARCHAR(40) NOT NULL DEFAULT 'point_in_polygon',
    membership_confidence       FLOAT       NOT NULL CHECK (
        membership_confidence >= 0 AND membership_confidence <= 1
    ),
    membership_reasons          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    membership_status           VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        membership_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    scope_version               VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (polygon_observation_id, site_observation_id, scope_version)
);

CREATE TABLE IF NOT EXISTS canonical_sites (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_key               CHAR(64)    NOT NULL,
    representative_site_observation_id UUID NOT NULL REFERENCES site_observations(id),
    representative_geom         geometry(Point, 4326) NOT NULL,
    task_family                 VARCHAR(20) NOT NULL,
    member_count                INTEGER     NOT NULL CHECK (member_count > 0),
    diameter_m                  FLOAT       NOT NULL CHECK (diameter_m >= 0),
    matching_method             VARCHAR(40) NOT NULL DEFAULT 'complete_link',
    match_confidence            FLOAT       NOT NULL CHECK (
        match_confidence >= 0 AND match_confidence <= 1
    ),
    match_reasons               JSONB       NOT NULL DEFAULT '[]'::jsonb,
    canonical_status            VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        canonical_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    canonical_version           VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (canonical_key, canonical_version)
);

CREATE TABLE IF NOT EXISTS canonical_site_memberships (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_site_id           UUID        NOT NULL REFERENCES canonical_sites(id) ON DELETE CASCADE,
    site_observation_id         UUID        NOT NULL REFERENCES site_observations(id) ON DELETE CASCADE,
    distance_to_representative_m FLOAT      NOT NULL CHECK (distance_to_representative_m >= 0),
    is_representative           BOOLEAN     NOT NULL DEFAULT FALSE,
    membership_status           VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        membership_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    canonical_version           VARCHAR(100) NOT NULL,
    reviewed_by                 VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_observation_id, canonical_version)
);

CREATE TABLE IF NOT EXISTS canonical_site_scope_memberships (
    id                          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    polygon_observation_id      UUID        NOT NULL REFERENCES site_scope_polygon_observations(id) ON DELETE CASCADE,
    canonical_site_id           UUID        NOT NULL REFERENCES canonical_sites(id) ON DELETE CASCADE,
    membership_method           VARCHAR(60) NOT NULL DEFAULT 'derived_point_in_polygon',
    membership_status           VARCHAR(40) NOT NULL DEFAULT 'suggested' CHECK (
        membership_status IN ('suggested', 'human_confirmed', 'human_rejected')
    ),
    canonical_version           VARCHAR(100) NOT NULL,
    scope_version               VARCHAR(100) NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (polygon_observation_id, canonical_site_id, canonical_version, scope_version)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_upload_id   UUID,
    source_file     VARCHAR(255)    NOT NULL,
    parser_version  VARCHAR(80)     NOT NULL,
    points_inserted INTEGER         NOT NULL DEFAULT 0 CHECK (points_inserted >= 0),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

ALTER TABLE import_batches
    ADD COLUMN IF NOT EXISTS raw_upload_id UUID;

-- Spatial index
CREATE INDEX IF NOT EXISTS idx_survey_history_geom
    ON survey_history USING GIST (geom);

-- Temporal index (newest-first)
CREATE INDEX IF NOT EXISTS idx_survey_history_timestamp
    ON survey_history (timestamp DESC);

-- Equipment-type lookup
CREATE INDEX IF NOT EXISTS idx_survey_history_equipment
    ON survey_history (equipment_type);

CREATE INDEX IF NOT EXISTS idx_survey_history_layout_family
    ON survey_history (layout_family);

CREATE INDEX IF NOT EXISTS idx_survey_history_parser_version
    ON survey_history (parser_version);

CREATE INDEX IF NOT EXISTS idx_survey_history_source_parser
    ON survey_history (source_file, parser_version);

CREATE INDEX IF NOT EXISTS idx_survey_history_import_batch
    ON survey_history (import_batch_id);

CREATE INDEX IF NOT EXISTS idx_survey_history_raw_upload
    ON survey_history (raw_upload_id);

CREATE INDEX IF NOT EXISTS idx_survey_history_site_lookup
    ON survey_history (parser_version, layout_family, source_file, site_name);

CREATE INDEX IF NOT EXISTS idx_raw_uploads_source_created
    ON raw_uploads (source_file, created_at DESC);

DROP INDEX IF EXISTS idx_raw_uploads_content_hash;

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_uploads_content_hash
    ON raw_uploads (content_hash);

CREATE INDEX IF NOT EXISTS idx_raw_placemarks_upload_version
    ON raw_placemarks (raw_upload_id, raw_parser_version);

CREATE INDEX IF NOT EXISTS idx_raw_placemarks_geometry_types
    ON raw_placemarks USING GIN (geometry_types);

CREATE INDEX IF NOT EXISTS idx_classification_decisions_role_family
    ON classification_decisions (business_role, task_family, rule_version);

CREATE INDEX IF NOT EXISTS idx_classification_decisions_review
    ON classification_decisions (classification_status, classification_confidence);

CREATE INDEX IF NOT EXISTS idx_camera_observations_geom
    ON camera_observations USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_camera_observations_version_family
    ON camera_observations (normalization_version, task_family);

CREATE INDEX IF NOT EXISTS idx_site_observations_geom
    ON site_observations USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_site_observations_version_key
    ON site_observations (normalization_version, site_key, task_family);

CREATE INDEX IF NOT EXISTS idx_direction_line_observations_geom
    ON direction_line_observations USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_direction_line_observations_review
    ON direction_line_observations (normalization_version, requires_review, binding_state);

CREATE INDEX IF NOT EXISTS idx_camera_direction_bindings_direction
    ON camera_direction_bindings (direction_line_observation_id, binding_version, binding_status);

CREATE INDEX IF NOT EXISTS idx_camera_site_relationships_camera
    ON camera_site_relationships (
        camera_observation_id, association_version, relationship_role, relationship_status
    );

CREATE INDEX IF NOT EXISTS idx_camera_site_relationships_site
    ON camera_site_relationships (
        site_observation_id, association_version, relationship_role, relationship_status
    );

CREATE INDEX IF NOT EXISTS idx_camera_site_review_items_open
    ON camera_site_review_items (association_version, review_status, review_code);

CREATE INDEX IF NOT EXISTS idx_camera_location_clusters_geom
    ON camera_location_clusters USING GIST (representative_geom);

CREATE INDEX IF NOT EXISTS idx_camera_location_clusters_version
    ON camera_location_clusters (cluster_version, cluster_status);

CREATE INDEX IF NOT EXISTS idx_camera_location_cluster_members_cluster
    ON camera_location_cluster_members (camera_location_cluster_id, cluster_version);

CREATE INDEX IF NOT EXISTS idx_site_camera_count_decisions_review
    ON site_camera_count_decisions (
        decision_version, requires_review, decision_status, camera_count_source
    );

CREATE INDEX IF NOT EXISTS idx_site_camera_generation_results_review
    ON site_camera_generation_results (
        generation_version, generation_status, requires_review, result_status
    );

CREATE INDEX IF NOT EXISTS idx_site_camera_generation_selections_tier
    ON site_camera_generation_selections (
        generation_result_id, selection_tier, selection_rank
    );

CREATE INDEX IF NOT EXISTS idx_site_scope_polygon_observations_geom
    ON site_scope_polygon_observations USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_site_scope_polygon_observations_review
    ON site_scope_polygon_observations (
        scope_version, scope_status, requires_review, decision_status
    );

CREATE INDEX IF NOT EXISTS idx_site_scope_polygon_memberships_site
    ON site_scope_polygon_memberships (site_observation_id, scope_version, membership_status);

CREATE INDEX IF NOT EXISTS idx_canonical_sites_geom
    ON canonical_sites USING GIST (representative_geom);

CREATE INDEX IF NOT EXISTS idx_canonical_sites_version
    ON canonical_sites (canonical_version, task_family, canonical_status);

CREATE INDEX IF NOT EXISTS idx_canonical_site_memberships_site
    ON canonical_site_memberships (site_observation_id, canonical_version, membership_status);

CREATE INDEX IF NOT EXISTS idx_canonical_site_scope_memberships_polygon
    ON canonical_site_scope_memberships (
        polygon_observation_id, canonical_version, scope_version, membership_status
    );

CREATE INDEX IF NOT EXISTS idx_import_batches_raw_upload
    ON import_batches (raw_upload_id);

CREATE INDEX IF NOT EXISTS idx_raw_upload_layer_reviews_scope
    ON raw_upload_layer_reviews (raw_upload_id, review_status, layer_path);


-- -----------------------------------------------------------
-- fn_get_clustered_reference_points
--
-- Selects one coherent historical camera layout for the physical site near
-- the requested coordinate. Historical layouts vote on the expected camera
-- count; the newest exact layout with the winning count supplies the output.
-- -----------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_get_clustered_reference_points(
    p_lat              FLOAT,
    p_lng              FLOAT,
    p_radius_m         FLOAT   DEFAULT 25.0,
    p_eps_m            FLOAT   DEFAULT 1.5,
    p_heading_bin_deg  FLOAT   DEFAULT 90.0,
    p_projected_srid   INT     DEFAULT 7855,
    p_layout_family    TEXT    DEFAULT 'ic',
    p_parser_version   TEXT    DEFAULT NULL,
    p_site_equivalence_m FLOAT DEFAULT 25.0
)
RETURNS TABLE (
    representative_lat  FLOAT,
    representative_lng  FLOAT,
    latest_azimuth      FLOAT,
    equipment_type      VARCHAR(20),
    layout_family       VARCHAR(40),
    source_group        VARCHAR(255),
    total_usage         BIGINT,
    time_decay_score    FLOAT,
    record_count        BIGINT,
    distance_m          FLOAT,
    latest_timestamp    TIMESTAMPTZ
)
LANGUAGE sql STABLE
AS $$
    WITH centre AS (
        -- Build the search centre in SRID 4326
        SELECT ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326) AS pt
    ),

    candidate_rows AS (
        -- The requested radius matches the user pin to historical SITE marks.
        -- Cameras linked to that site may legitimately be much farther away.
        SELECT
            sh.id,
            (
                COALESCE(sh.raw_upload_id::TEXT, sh.source_file, 'legacy')
                || '|' || COALESCE(sh.site_name, '')
                || '|' || COALESCE(sh.site_lat::TEXT, '')
                || '|' || COALESCE(sh.site_lng::TEXT, '')
            ) AS layout_key,
            sh.raw_upload_id,
            sh.source_file,
            sh.geom,
            ST_Transform(sh.geom, p_projected_srid) AS projected_geom,
            sh.azimuth,
            sh.equipment_type,
            sh.layout_family,
            sh.source_group,
            sh.site_name,
            sh.site_source_group,
            sh.site_lat,
            sh.site_lng,
            sh.site_distance_m,
            sh.usage_count,
            sh.timestamp,
            CASE
                WHEN sh.site_lat IS NOT NULL AND sh.site_lng IS NOT NULL THEN
                    ST_Distance(
                        ST_SetSRID(ST_MakePoint(sh.site_lng, sh.site_lat), 4326)::geography,
                        c.pt::geography
                    )
                ELSE ST_Distance(sh.geom::geography, c.pt::geography)
            END AS site_pin_distance_m,
            POWER(
                0.5,
                GREATEST(
                    EXTRACT(EPOCH FROM (NOW() - sh.timestamp))
                    / (365.25 * 24 * 60 * 60)
                    / 3.0,
                    0
                )
            ) AS time_weight
        FROM survey_history sh, centre c
        WHERE sh.equipment_type IN ('camera_dir', 'camera_omni')
          AND (p_layout_family IS NULL OR sh.layout_family = p_layout_family)
          AND (p_parser_version IS NULL OR sh.parser_version = p_parser_version)
          AND (
                (
                    sh.site_lat IS NOT NULL
                    AND sh.site_lng IS NOT NULL
                    AND ST_DWithin(
                        ST_SetSRID(ST_MakePoint(sh.site_lng, sh.site_lat), 4326)::geography,
                        c.pt::geography,
                        p_radius_m
                    )
                )
                OR (
                    (sh.site_lat IS NULL OR sh.site_lng IS NULL)
                    AND ST_DWithin(sh.geom::geography, c.pt::geography, p_radius_m)
                )
          )
    ),

    candidate_layouts AS (
        SELECT
            cr.layout_key,
            cr.raw_upload_id,
            cr.source_file,
            cr.site_name,
            cr.site_lat,
            cr.site_lng,
            MIN(cr.site_pin_distance_m) AS site_pin_distance_m,
            MAX(cr.timestamp) AS latest_timestamp,
            MAX(cr.time_weight) AS layout_weight
        FROM candidate_rows cr
        GROUP BY
            cr.layout_key,
            cr.raw_upload_id,
            cr.source_file,
            cr.site_name,
            cr.site_lat,
            cr.site_lng
    ),

    nearest_layout AS (
        -- First identify the single site the user most likely pinned.
        SELECT cl.*
        FROM candidate_layouts cl
        ORDER BY cl.site_pin_distance_m, cl.latest_timestamp DESC, cl.layout_key
        LIMIT 1
    ),

    eligible_layouts AS (
        -- A site may appear in several survey files with slightly different
        -- marker coordinates. Marks within 25m are the same physical site,
        -- but distinct tasks inside one upload remain separate layouts.
        SELECT cl.*
        FROM candidate_layouts cl
        CROSS JOIN nearest_layout nl
        WHERE cl.layout_key = nl.layout_key
           OR (
                cl.raw_upload_id IS DISTINCT FROM nl.raw_upload_id
                AND cl.site_lat IS NOT NULL
                AND cl.site_lng IS NOT NULL
                AND nl.site_lat IS NOT NULL
                AND nl.site_lng IS NOT NULL
                AND ST_DWithin(
                    ST_SetSRID(ST_MakePoint(cl.site_lng, cl.site_lat), 4326)::geography,
                    ST_SetSRID(ST_MakePoint(nl.site_lng, nl.site_lat), 4326)::geography,
                    p_site_equivalence_m
                )
           )
    ),

    layout_camera_clusters AS (
        -- Count physical camera locations within each complete historical
        -- layout. Duplicate marks within 1.5m count as one camera location.
        SELECT
            cr.*,
            ST_ClusterDBSCAN(
                cr.projected_geom,
                eps := p_eps_m,
                minpoints := 1
            ) OVER (PARTITION BY cr.layout_key ORDER BY cr.id) AS layout_camera_cluster_id
        FROM candidate_rows cr
        JOIN eligible_layouts el
            ON el.layout_key = cr.layout_key
    ),

    layout_counts AS (
        SELECT
            lcc.layout_key,
            COUNT(DISTINCT lcc.layout_camera_cluster_id) AS camera_count,
            MAX(lcc.timestamp) AS latest_timestamp,
            MAX(lcc.time_weight) AS layout_weight,
            MIN(lcc.site_pin_distance_m) AS site_pin_distance_m
        FROM layout_camera_clusters lcc
        GROUP BY lcc.layout_key
    ),

    count_scores AS (
        -- Each survey layout gets one recency-weighted vote for its camera
        -- count. Re-uploading identical content does not add another layout.
        SELECT
            lc.camera_count,
            SUM(lc.layout_weight) AS count_score,
            MAX(lc.latest_timestamp) AS latest_timestamp
        FROM layout_counts lc
        GROUP BY lc.camera_count
    ),

    target_count AS (
        SELECT cs.camera_count
        FROM count_scores cs
        ORDER BY cs.count_score DESC, cs.latest_timestamp DESC, cs.camera_count
        LIMIT 1
    ),

    selected_layout AS (
        -- Keep the output coherent: all returned cameras come from one exact
        -- historical layout, never a union of incompatible surveys.
        SELECT lc.layout_key
        FROM layout_counts lc
        CROSS JOIN target_count tc
        WHERE lc.camera_count = tc.camera_count
        ORDER BY
            lc.latest_timestamp DESC,
            lc.layout_weight DESC,
            lc.site_pin_distance_m,
            lc.layout_key
        LIMIT 1
    ),

    filtered AS (
        SELECT lcc.*
        FROM layout_camera_clusters lcc
        JOIN selected_layout sl
            ON sl.layout_key = lcc.layout_key
    ),

    spatial_clustered AS (
        -- DBSCAN in the local projected CRS selected by the query longitude.
        SELECT
            f.*,
            ST_ClusterDBSCAN(
                f.projected_geom,
                eps := p_eps_m,
                minpoints := 1
            ) OVER (ORDER BY f.id) AS spatial_cluster_id
        FROM filtered f
    ),

    heading_vectors AS (
        SELECT
            sc.spatial_cluster_id,
            SUM(SIN(RADIANS(sc.azimuth))) AS heading_y,
            SUM(COS(RADIANS(sc.azimuth))) AS heading_x
        FROM spatial_clustered sc
        WHERE sc.azimuth IS NOT NULL
        GROUP BY sc.spatial_cluster_id
    ),

    heading_frames AS (
        -- A circular mean gives each spatial cluster its own heading frame.
        -- That avoids splitting near 0/360 or any other arbitrary global boundary.
        SELECT
            hv.spatial_cluster_id,
            (
                DEGREES(ATAN2(hv.heading_y, hv.heading_x)) + 360.0
            )
            - 360.0 * FLOOR(
                (DEGREES(ATAN2(hv.heading_y, hv.heading_x)) + 360.0) / 360.0
            ) AS reference_heading
        FROM heading_vectors hv
    ),

    heading_clustered AS (
        -- Split cameras on the same pole by broad field-of-view direction.
        -- NULL headings are their own fallback group and stay NULL in output.
        SELECT
            sc.*,
            CASE
                WHEN sc.azimuth IS NULL THEN NULL
                ELSE FLOOR(
                    (
                        hd.raw_heading_delta
                        - 360.0 * FLOOR(hd.raw_heading_delta / 360.0)
                    ) / p_heading_bin_deg
                )::INT
            END AS heading_bin
        FROM spatial_clustered sc
        LEFT JOIN heading_frames hf
            ON hf.spatial_cluster_id = sc.spatial_cluster_id
        CROSS JOIN LATERAL (
            SELECT
                sc.azimuth - COALESCE(hf.reference_heading, 0.0)
                + (p_heading_bin_deg / 2.0)
                + 360.0 AS raw_heading_delta
        ) hd
    ),

    grouped AS (
        SELECT
            hc.spatial_cluster_id,
            SUM(hc.usage_count) AS total_usage,
            SUM(hc.usage_count * hc.time_weight) AS time_decay_score,
            COUNT(*) AS record_count,
            MAX(hc.timestamp) AS latest_timestamp,
            ST_SetSRID(
                ST_MakePoint(
                    SUM(ST_X(hc.projected_geom) * hc.usage_count * hc.time_weight)
                    / NULLIF(SUM(hc.usage_count * hc.time_weight), 0),
                    SUM(ST_Y(hc.projected_geom) * hc.usage_count * hc.time_weight)
                    / NULLIF(SUM(hc.usage_count * hc.time_weight), 0)
                ),
                p_projected_srid
            ) AS weighted_center_geom
        FROM heading_clustered hc
        GROUP BY hc.spatial_cluster_id
    ),

    representatives AS (
        SELECT
            hc.geom AS representative_geom,
            hc.azimuth AS latest_azimuth,
            hc.equipment_type,
            hc.layout_family,
            hc.source_group,
            hc.site_name,
            hc.site_source_group,
            hc.site_lat,
            hc.site_lng,
            hc.site_distance_m,
            g.total_usage,
            g.time_decay_score,
            g.record_count,
            g.latest_timestamp,
            ROW_NUMBER() OVER (
                PARTITION BY hc.spatial_cluster_id
                ORDER BY
                    hc.timestamp DESC,
                    hc.id DESC
            ) AS rn
        FROM heading_clustered hc
        JOIN grouped g
            ON g.spatial_cluster_id = hc.spatial_cluster_id
    )

    SELECT
        ST_Y(r.representative_geom)::FLOAT                AS representative_lat,
        ST_X(r.representative_geom)::FLOAT                AS representative_lng,
        r.latest_azimuth                                  AS latest_azimuth,
        r.equipment_type                                  AS equipment_type,
        r.layout_family                                   AS layout_family,
        r.source_group                                    AS source_group,
        r.total_usage                                     AS total_usage,
        r.time_decay_score::FLOAT                         AS time_decay_score,
        r.record_count                                    AS record_count,
        ST_Distance(
            r.representative_geom::geography,
            (SELECT pt::geography FROM centre)
        )::FLOAT                                          AS distance_m,
        r.latest_timestamp                                AS latest_timestamp
    FROM representatives r
    WHERE r.rn = 1
    ORDER BY r.time_decay_score DESC, distance_m;
$$;
