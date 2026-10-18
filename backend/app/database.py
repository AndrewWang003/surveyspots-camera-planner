"""Async database layer backed by *asyncpg*."""

from __future__ import annotations

import logging
import hashlib
import json
from pathlib import Path
from uuid import UUID

from app.business_classifier import ClassificationDecision
from app.canonical_site import CanonicalSiteEvidence, build_canonical_sites
from app.camera_site_relationships import CameraSiteRelationshipBundle
from app.camera_location_clustering import (
    CameraLocationEvidence,
    build_camera_location_clusters,
)
from app.camera_count_selector import (
    SiteCameraCountEvidence,
    select_site_camera_count,
)
from app.camera_quantity import infer_camera_quantity
from app.camera_generation_selector import (
    CameraGenerationCandidate,
    select_camera_locations,
)
from app.direction_geometry import haversine_metres
from app.historical_layer_review import LayerReviewSelection, normalise_layer_review_selection
from app.models import SurveyPoint
from app.observation_builder import ObservationBundle
from app.raw_placemark_parser import RawPlacemarkRecord
from app.polygon_scope import PolygonScopeBundle
from app.task_family import task_families_compatible

import asyncpg

from app.config import get_projected_srid, settings

logger = logging.getLogger(__name__)

# Module-level connection pool -------------------------------------------------
_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    """Create the asyncpg connection pool from ``settings.DATABASE_URL``."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        logger.warning("Connection pool already initialised — skipping.")
        return

    logger.info("Creating asyncpg connection pool …")
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
    )
    await _ensure_schema()
    logger.info("Connection pool ready.")


async def close_db() -> None:
    """Gracefully close the connection pool."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        logger.info("Closing asyncpg connection pool …")
        await _pool.close()
        _pool = None
        logger.info("Connection pool closed.")


async def get_pool() -> asyncpg.Pool:
    """Return the current connection pool.

    Raises
    ------
    RuntimeError
        If the pool has not been initialised yet.
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Call init_db() during application startup."
        )
    return _pool


async def _ensure_schema() -> None:
    """Apply additive schema updates needed by the current backend."""
    if _pool is None:
        return

    init_sql_path = Path(__file__).resolve().parents[1] / "sql" / "init.sql"
    init_sql = init_sql_path.read_text()
    function_start = init_sql.index("CREATE OR REPLACE FUNCTION fn_get_clustered_reference_points")
    function_end = init_sql.index("$$;", function_start) + len("$$;")
    function_sql = init_sql[function_start:function_end]

    ddl = f"""
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
            folder_path         TEXT[]      NOT NULL DEFAULT '{{}}',
            placemark_name      TEXT        NOT NULL DEFAULT '',
            description         TEXT        NOT NULL DEFAULT '',
            style_url           TEXT        NOT NULL DEFAULT '',
            style_id            TEXT        NOT NULL DEFAULT '',
            style_definition    JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
            extended_data       JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
            geometry_types      TEXT[]      NOT NULL DEFAULT '{{}}',
            geometry            JSONB       NOT NULL DEFAULT '[]'::jsonb,
            raw_coordinates     TEXT[]      NOT NULL DEFAULT '{{}}',
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
            decision_metadata           JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
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
                task_family IN (
                    'ic', 'od', 'ipod', 'hik', 'atc', 'unknown', 'mixed'
                )
            );

        ALTER TABLE classification_decisions
            ADD COLUMN IF NOT EXISTS model_version VARCHAR(120) NOT NULL DEFAULT '';

        ALTER TABLE classification_decisions
            ADD COLUMN IF NOT EXISTS decision_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb;

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

        ALTER TABLE site_camera_count_decisions
            DROP CONSTRAINT IF EXISTS site_camera_count_decisions_camera_count_source_check;
        ALTER TABLE site_camera_count_decisions
            ADD CONSTRAINT site_camera_count_decisions_camera_count_source_check CHECK (
                camera_count_source IN (
                    'explicit_description',
                    'explicit_description_conflict_primary',
                    'historical_mode',
                    'insufficient_history'
                )
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
            style_evidence              JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
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

        {function_sql}
    """

    async with _pool.acquire() as conn:
        await conn.execute(ddl)


# -- Query helpers -------------------------------------------------------------


async def store_raw_upload(
    source_filename: str,
    content: bytes,
    content_type: str | None = None,
    uploaded_by: str = "",
) -> UUID:
    """Persist an original upload once and return its content identity.

    Exact byte-for-byte repeats are the same evidence, even if a user uploads
    the file again.  The advisory lock keeps concurrent duplicate uploads from
    creating two raw records before either transaction commits.
    """
    pool = await get_pool()
    content_hash = hashlib.sha256(content).hexdigest()
    query = """
        INSERT INTO raw_uploads (
            source_file,
            content_hash,
            content,
            content_type,
            uploaded_by
        )
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id;
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1));",
                content_hash,
            )
            raw_upload_id = await conn.fetchval(
                """
                SELECT id
                FROM raw_uploads
                WHERE content_hash = $1
                ORDER BY created_at, id
                LIMIT 1;
                """,
                content_hash,
            )
            if raw_upload_id is None:
                raw_upload_id = await conn.fetchval(
                    query,
                    source_filename,
                    content_hash,
                    content,
                    content_type or "application/vnd.google-earth.kml+xml",
                    uploaded_by,
                )
    logger.info(
        "Stored raw upload %s for '%s' (%s).",
        raw_upload_id,
        source_filename,
        content_hash,
    )
    return raw_upload_id


async def fetch_latest_raw_uploads() -> list[asyncpg.Record]:
    """Return the newest unique raw upload for each source filename."""
    pool = await get_pool()
    query = """
        WITH latest_by_source AS (
            SELECT DISTINCT ON (source_file)
                id,
                source_file,
                content_hash,
                content,
                content_type,
                uploaded_by,
                created_at
            FROM raw_uploads
            ORDER BY source_file, created_at DESC, id DESC
        )
        SELECT DISTINCT ON (latest.content_hash)
            canonical.id,
            canonical.source_file,
            canonical.content,
            canonical.content_type,
            canonical.uploaded_by,
            canonical.created_at
        FROM latest_by_source latest
        CROSS JOIN LATERAL (
            SELECT ru.*
            FROM raw_uploads ru
            WHERE ru.content_hash = latest.content_hash
            ORDER BY ru.created_at, ru.id
            LIMIT 1
        ) canonical
        ORDER BY latest.content_hash, latest.created_at DESC, latest.id DESC;
    """
    async with pool.acquire() as conn:
        return await conn.fetch(query)


async def fetch_historical_review_items(raw_upload_id: UUID) -> dict:
    """Return unresolved review evidence for one historical upload."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        upload = await conn.fetchrow(
            """
            SELECT id, source_file
            FROM raw_uploads
            WHERE id = $1;
            """,
            raw_upload_id,
        )
        if upload is None:
            return {
                "raw_upload_id": str(raw_upload_id),
                "source_file": "",
                "items": [],
            }

        site_rows = await conn.fetch(
            """
            SELECT
                so.id,
                rp.placemark_name,
                so.task_family,
                ST_X(so.geom) AS lng,
                ST_Y(so.geom) AS lat
            FROM site_observations so
            JOIN raw_placemarks rp ON rp.id = so.raw_placemark_id
            WHERE rp.raw_upload_id = $1
              AND so.normalization_version = $2
            ORDER BY so.task_family, rp.placemark_name, so.id;
            """,
            raw_upload_id,
            settings.OBSERVATION_NORMALIZATION_VERSION,
        )
        sites = [
            {
                "site_observation_id": str(row["id"]),
                "name": row["placemark_name"],
                "task_family": row["task_family"],
                "position": (float(row["lng"]), float(row["lat"])),
                "lat": float(row["lat"]),
                "lng": float(row["lng"]),
            }
            for row in site_rows
        ]

        camera_rows = await conn.fetch(
            """
            SELECT
                cri.id AS review_item_id,
                cri.review_code,
                cri.review_reasons,
                cri.candidate_distance_m,
                candidate_site.id AS candidate_site_observation_id,
                candidate_rp.placemark_name AS candidate_site_name,
                candidate_site.task_family AS candidate_site_task_family,
                co.id AS camera_observation_id,
                co.placemark_name AS camera_name,
                co.task_family AS camera_task_family,
                camera_rp.style_url,
                camera_rp.style_definition,
                ST_X(co.geom) AS lng,
                ST_Y(co.geom) AS lat
            FROM camera_site_review_items cri
            JOIN camera_observations co ON co.id = cri.camera_observation_id
            JOIN raw_placemarks camera_rp ON camera_rp.id = co.raw_placemark_id
            LEFT JOIN site_observations candidate_site
              ON candidate_site.id = cri.candidate_site_observation_id
            LEFT JOIN raw_placemarks candidate_rp
              ON candidate_rp.id = candidate_site.raw_placemark_id
            WHERE camera_rp.raw_upload_id = $1
              AND cri.association_version = $2
              AND cri.review_status = 'open'
            ORDER BY cri.created_at, cri.id;
            """,
            raw_upload_id,
            settings.CAMERA_SITE_RELATIONSHIP_VERSION,
        )

        items: list[dict] = []
        for row in camera_rows:
            camera_position = (float(row["lng"]), float(row["lat"]))
            recommended_id = (
                str(row["candidate_site_observation_id"])
                if row["candidate_site_observation_id"] is not None
                else None
            )
            items.append(
                {
                    "item_type": "camera_site",
                    "title": row["camera_name"] or "Camera Location",
                    "reason": _review_reason_text(
                        row["review_code"],
                        row["review_reasons"],
                    ),
                    "source_file": upload["source_file"],
                    "raw_upload_id": str(upload["id"]),
                    "camera_observation_id": str(row["camera_observation_id"]),
                    "camera_name": row["camera_name"] or "Camera Location",
                    "camera_task_family": row["camera_task_family"],
                    "lat": float(row["lat"]),
                    "lng": float(row["lng"]),
                    "style_url": row["style_url"],
                    "style_definition": _json_object(row["style_definition"]),
                    "current_site_observation_id": recommended_id,
                    "current_site_name": row["candidate_site_name"] or "",
                    "current_site_task_family": (
                        row["candidate_site_task_family"] or ""
                    ),
                    "suggested_relationship_role": "primary",
                    "candidate_sites": _review_site_candidates(
                        sites,
                        camera_position,
                        row["camera_task_family"],
                        recommended_id,
                    ),
                }
            )

    for index, item in enumerate(items, start=1):
        item["review_id"] = f"C-{index:03d}"
    return {
        "raw_upload_id": str(upload["id"]),
        "source_file": upload["source_file"],
        "items": items,
    }


def _review_site_candidates(
    sites: list[dict],
    camera_position: tuple[float, float],
    camera_task_family: str,
    recommended_site_id: str | None,
) -> list[dict]:
    ranked = []
    for site in sites:
        distance = haversine_metres(camera_position, site["position"])
        same_family = task_families_compatible(
            camera_task_family,
            site["task_family"],
        )
        ranked.append(
            (
                0 if str(site["site_observation_id"]) == recommended_site_id else 1,
                0 if same_family else 1,
                distance,
                site["name"],
                site,
            )
        )
    return [
        {
            "site_observation_id": site["site_observation_id"],
            "name": site["name"],
            "task_family": site["task_family"],
            "distance_m": round(distance, 1),
            "lat": site["lat"],
            "lng": site["lng"],
            "is_recommended": str(site["site_observation_id"]) == recommended_site_id,
        }
        for _, _, distance, _, site in sorted(ranked)[:12]
    ]


def _review_reason_text(review_code: str, reasons_value) -> str:
    if isinstance(reasons_value, str):
        try:
            reasons = json.loads(reasons_value)
        except json.JSONDecodeError:
            reasons = [reasons_value]
    else:
        reasons = reasons_value or []
    if reasons:
        return " ".join(str(reason) for reason in reasons)
    return review_code.replace("_", " ").capitalize()


async def submit_historical_review_decisions(
    raw_upload_id: UUID,
    decisions: list[dict],
    reviewed_by: str,
) -> tuple[int, int]:
    """Persist human Camera-Site review decisions and rebuild suggestions."""
    pool = await get_pool()
    saved_count = 0
    dismissed_count = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for decision in decisions:
                camera_id = UUID(decision["camera_observation_id"])
                ignored = bool(decision.get("ignored"))
                role = decision.get("relationship_role") or "primary"
                site_id_value = decision.get("selected_site_observation_id")
                if ignored or role == "unassigned" or not site_id_value:
                    await conn.execute(
                        """
                        UPDATE camera_site_review_items cri
                        SET review_status = 'human_dismissed',
                            reviewed_by = $3,
                            reviewed_at = NOW(),
                            updated_at = NOW()
                        FROM camera_observations co
                        JOIN raw_placemarks rp ON rp.id = co.raw_placemark_id
                        WHERE cri.camera_observation_id = co.id
                          AND rp.raw_upload_id = $1
                          AND cri.camera_observation_id = $2
                          AND cri.association_version = $4
                          AND cri.review_status = 'open';
                        """,
                        raw_upload_id,
                        camera_id,
                        reviewed_by,
                        settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                    )
                    dismissed_count += 1
                    continue

                site_id = UUID(site_id_value)
                distance = await conn.fetchval(
                    """
                    SELECT ST_Distance(co.geom::geography, so.geom::geography)
                    FROM camera_observations co
                    JOIN raw_placemarks camera_rp ON camera_rp.id = co.raw_placemark_id
                    JOIN site_observations so ON so.id = $3
                    JOIN raw_placemarks site_rp ON site_rp.id = so.raw_placemark_id
                    WHERE co.id = $2
                      AND camera_rp.raw_upload_id = $1
                      AND site_rp.raw_upload_id = $1;
                    """,
                    raw_upload_id,
                    camera_id,
                    site_id,
                )
                if distance is None:
                    continue

                if role == "primary":
                    await conn.execute(
                        """
                        UPDATE camera_site_relationships
                        SET relationship_status = 'human_rejected',
                            reviewed_by = $4,
                            reviewed_at = NOW(),
                            updated_at = NOW()
                        WHERE camera_observation_id = $1
                          AND site_observation_id <> $2
                          AND association_version = $3
                          AND relationship_role = 'primary'
                          AND relationship_status = 'suggested';
                        """,
                        camera_id,
                        site_id,
                        settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                        reviewed_by,
                    )

                await conn.execute(
                    """
                    INSERT INTO camera_site_relationships (
                        camera_observation_id,
                        site_observation_id,
                        relationship_role,
                        distance_m,
                        association_method,
                        is_named_association,
                        relationship_confidence,
                        relationship_reasons,
                        relationship_status,
                        association_version,
                        reviewed_by,
                        reviewed_at
                    )
                    VALUES (
                        $1, $2, $3, $4, 'spatial_nearest', FALSE,
                        1.0, $5::jsonb, 'human_confirmed', $6, $7, NOW()
                    )
                    ON CONFLICT (
                        camera_observation_id,
                        site_observation_id,
                        association_version
                    )
                    DO UPDATE SET
                        relationship_role = EXCLUDED.relationship_role,
                        distance_m = EXCLUDED.distance_m,
                        relationship_confidence = EXCLUDED.relationship_confidence,
                        relationship_reasons = EXCLUDED.relationship_reasons,
                        relationship_status = 'human_confirmed',
                        reviewed_by = EXCLUDED.reviewed_by,
                        reviewed_at = NOW(),
                        updated_at = NOW();
                    """,
                    camera_id,
                    site_id,
                    role,
                    float(distance),
                    json.dumps(
                        [
                            "Human review assigned this Camera to this Site.",
                            f"Selected relationship role: {role}.",
                        ]
                    ),
                    settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                    reviewed_by,
                )
                await conn.execute(
                    """
                    UPDATE camera_site_review_items cri
                    SET review_status = 'human_resolved',
                        candidate_site_observation_id = $3,
                        reviewed_by = $4,
                        reviewed_at = NOW(),
                        updated_at = NOW()
                    FROM camera_observations co
                    JOIN raw_placemarks rp ON rp.id = co.raw_placemark_id
                    WHERE cri.camera_observation_id = co.id
                      AND rp.raw_upload_id = $1
                      AND cri.camera_observation_id = $2
                      AND cri.association_version = $5
                      AND cri.review_status = 'open';
                    """,
                    raw_upload_id,
                    camera_id,
                    site_id,
                    reviewed_by,
                    settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                )
                saved_count += 1

    await rebuild_site_camera_count_decisions()
    await rebuild_site_camera_generation_results()
    return saved_count, dismissed_count


async def store_raw_upload_layer_reviews(
    raw_upload_id: UUID,
    layer_paths: list[str],
    selected_layers: dict[str, str | LayerReviewSelection],
    reviewed_by: str,
) -> None:
    """Persist selected and excluded Layer decisions from upload review."""
    pool = await get_pool()
    params = [
        (
            raw_upload_id,
            layer_path,
            "selected" if layer_path in selected_layers else "excluded",
            (
                normalise_layer_review_selection(selected_layers[layer_path]).selected_family
                if layer_path in selected_layers
                else None
            ),
            json.dumps(
                list(
                    normalise_layer_review_selection(
                        selected_layers[layer_path],
                    ).review_tags
                )
                if layer_path in selected_layers
                else []
            ),
            reviewed_by,
        )
        for layer_path in sorted(set(layer_paths))
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM raw_upload_layer_reviews WHERE raw_upload_id = $1;",
                raw_upload_id,
            )
            if params:
                await conn.executemany(
                    """
                    INSERT INTO raw_upload_layer_reviews (
                        raw_upload_id,
                        layer_path,
                        review_status,
                        selected_family,
                        review_tags,
                        reviewed_by
                    )
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6);
                    """,
                    params,
                )


async def fetch_selected_raw_upload_layers(
    raw_upload_id: UUID,
) -> dict[str, LayerReviewSelection] | None:
    """Return reviewed selected Layers, or ``None`` when no review exists."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT layer_path, review_status, selected_family, review_tags
        FROM raw_upload_layer_reviews
        WHERE raw_upload_id = $1
        ORDER BY layer_path;
        """,
        raw_upload_id,
    )
    if not rows:
        return None
    return {
        row["layer_path"]: LayerReviewSelection(
            selected_family=row["selected_family"],
            review_tags=_json_array_tuple(row["review_tags"]),
        )
        for row in rows
        if row["review_status"] == "selected"
    }


def _json_array_tuple(value) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        value = json.loads(value)
    return tuple(value)


def _json_object(value) -> dict:
    if not value:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


async def reconcile_suggested_classification_scope(
    raw_upload_id: UUID,
    selected_placemark_indices: list[int],
) -> None:
    """Remove stale rule suggestions from Layers excluded by the latest review."""
    pool = await get_pool()
    await pool.execute(
        """
        DELETE FROM classification_decisions cd
        USING raw_placemarks rp
        WHERE cd.raw_placemark_id = rp.id
          AND rp.raw_upload_id = $1
          AND rp.raw_parser_version = $2
          AND cd.rule_version = $3
          AND cd.classification_status = 'suggested'
          AND NOT (rp.placemark_index = ANY($4::integer[]));
        """,
        raw_upload_id,
        settings.RAW_PLACEMARK_PARSER_VERSION,
        settings.CLASSIFICATION_RULE_VERSION,
        selected_placemark_indices,
    )


async def store_raw_placemarks(
    raw_upload_id: UUID,
    placemarks: list[RawPlacemarkRecord],
) -> int:
    """Persist one parser version of the raw Placemark layer idempotently."""
    if not placemarks:
        return 0

    pool = await get_pool()
    query = """
        INSERT INTO raw_placemarks (
            raw_upload_id,
            placemark_index,
            folder_path,
            placemark_name,
            description,
            style_url,
            style_id,
            style_definition,
            extended_data,
            geometry_types,
            geometry,
            raw_coordinates,
            raw_kml_fragment,
            raw_parser_version
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7,
            $8::jsonb, $9::jsonb, $10, $11::jsonb, $12, $13, $14
        )
        ON CONFLICT (raw_upload_id, placemark_index, raw_parser_version)
        DO UPDATE SET
            folder_path = EXCLUDED.folder_path,
            placemark_name = EXCLUDED.placemark_name,
            description = EXCLUDED.description,
            style_url = EXCLUDED.style_url,
            style_id = EXCLUDED.style_id,
            style_definition = EXCLUDED.style_definition,
            extended_data = EXCLUDED.extended_data,
            geometry_types = EXCLUDED.geometry_types,
            geometry = EXCLUDED.geometry,
            raw_coordinates = EXCLUDED.raw_coordinates,
            raw_kml_fragment = EXCLUDED.raw_kml_fragment;
    """
    params = [
        (
            raw_upload_id,
            placemark.placemark_index,
            list(placemark.folder_path),
            placemark.placemark_name,
            placemark.description,
            placemark.style_url,
            placemark.style_id,
            json.dumps(placemark.style_definition),
            json.dumps(placemark.extended_data),
            list(placemark.geometry_types),
            json.dumps(placemark.geometry),
            list(placemark.raw_coordinates),
            placemark.raw_kml_fragment,
            settings.RAW_PLACEMARK_PARSER_VERSION,
        )
        for placemark in placemarks
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(query, params)

    logger.info(
        "Stored %d raw Placemark record(s) for upload %s using %s.",
        len(placemarks),
        raw_upload_id,
        settings.RAW_PLACEMARK_PARSER_VERSION,
    )
    return len(placemarks)


async def store_classification_decisions(
    raw_upload_id: UUID,
    decisions: list[ClassificationDecision],
) -> int:
    """Persist versioned rule suggestions without overwriting human decisions."""
    if not decisions:
        return 0

    pool = await get_pool()
    query = """
        INSERT INTO classification_decisions (
            raw_placemark_id,
            business_role,
            task_family,
            required_camera_count,
            camera_count_source,
            classification_confidence,
            classification_reasons,
            classification_method,
            classification_status,
            model_version,
            decision_metadata,
            rule_version
        )
        SELECT
            rp.id, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11,
            $12::jsonb, $13
        FROM raw_placemarks rp
        WHERE rp.raw_upload_id = $1
          AND rp.placemark_index = $2
          AND rp.raw_parser_version = $14
        ON CONFLICT (raw_placemark_id, rule_version)
        DO UPDATE SET
            business_role = EXCLUDED.business_role,
            task_family = EXCLUDED.task_family,
            required_camera_count = EXCLUDED.required_camera_count,
            camera_count_source = EXCLUDED.camera_count_source,
            classification_confidence = EXCLUDED.classification_confidence,
            classification_reasons = EXCLUDED.classification_reasons,
            classification_method = EXCLUDED.classification_method,
            classification_status = EXCLUDED.classification_status,
            model_version = EXCLUDED.model_version,
            decision_metadata = EXCLUDED.decision_metadata,
            updated_at = NOW()
        WHERE classification_decisions.classification_status = 'suggested';
    """
    params = [
        (
            raw_upload_id,
            decision.placemark_index,
            decision.business_role,
            decision.task_family,
            decision.required_camera_count,
            decision.camera_count_source,
            decision.classification_confidence,
            json.dumps(decision.classification_reasons),
            decision.classification_method,
            decision.classification_status,
            decision.model_version,
            json.dumps(decision.decision_metadata or {}),
            settings.CLASSIFICATION_RULE_VERSION,
            settings.RAW_PLACEMARK_PARSER_VERSION,
        )
        for decision in decisions
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(query, params)
            stored_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM classification_decisions cd
                JOIN raw_placemarks rp ON rp.id = cd.raw_placemark_id
                WHERE rp.raw_upload_id = $1
                  AND cd.rule_version = $2;
                """,
                raw_upload_id,
                settings.CLASSIFICATION_RULE_VERSION,
            )
    return int(stored_count)


async def store_observation_bundle(
    raw_upload_id: UUID,
    bundle: ObservationBundle,
) -> tuple[int, int, int]:
    """Persist versioned Camera, Direction Line, and generated bindings."""
    pool = await get_pool()
    camera_query = """
        INSERT INTO camera_observations (
            raw_placemark_id,
            classification_decision_id,
            placemark_name,
            task_family,
            source_position,
            geom,
            location_method,
            classification_confidence,
            normalization_version
        )
        SELECT
            rp.id, cd.id, $3, $4, $5::jsonb,
            ST_SetSRID(ST_MakePoint($6, $7), 4326), $8, $9, $10
        FROM raw_placemarks rp
        JOIN classification_decisions cd ON cd.raw_placemark_id = rp.id
        WHERE rp.raw_upload_id = $1
          AND rp.placemark_index = $2
          AND rp.raw_parser_version = $11
          AND cd.rule_version = $12
        ON CONFLICT (classification_decision_id, normalization_version)
        DO UPDATE SET
            placemark_name = EXCLUDED.placemark_name,
            task_family = EXCLUDED.task_family,
            source_position = EXCLUDED.source_position,
            geom = EXCLUDED.geom,
            location_method = EXCLUDED.location_method,
            classification_confidence = EXCLUDED.classification_confidence,
            updated_at = NOW();
    """
    camera_params = [
        (
            raw_upload_id,
            camera.placemark_index,
            camera.name,
            camera.task_family,
            json.dumps(camera.position),
            camera.position[0],
            camera.position[1],
            camera.location_method,
            camera.classification_confidence,
            settings.OBSERVATION_NORMALIZATION_VERSION,
            settings.RAW_PLACEMARK_PARSER_VERSION,
            settings.CLASSIFICATION_RULE_VERSION,
        )
        for camera in bundle.cameras
    ]

    direction_query = """
        INSERT INTO direction_line_observations (
            raw_placemark_id,
            classification_decision_id,
            placemark_name,
            task_family,
            source_positions,
            geom,
            point_count,
            camera_anchor_geom,
            boundary_a_bearing,
            boundary_b_bearing,
            suggested_centre_bearing,
            suggested_coverage_angle,
            geometry_status,
            binding_state,
            requires_review,
            classification_confidence,
            normalization_version
        )
        SELECT
            rp.id, cd.id, $3, $4, $5::jsonb,
            ST_GeomFromText($6, 4326), $7,
            CASE
                WHEN $8::float IS NULL THEN NULL
                ELSE ST_SetSRID(ST_MakePoint($8, $9), 4326)
            END,
            $10, $11, $12, $13, $14, $15, $16, $17, $18
        FROM raw_placemarks rp
        JOIN classification_decisions cd ON cd.raw_placemark_id = rp.id
        WHERE rp.raw_upload_id = $1
          AND rp.placemark_index = $2
          AND rp.raw_parser_version = $19
          AND cd.rule_version = $20
        ON CONFLICT (classification_decision_id, normalization_version)
        DO UPDATE SET
            placemark_name = EXCLUDED.placemark_name,
            task_family = EXCLUDED.task_family,
            source_positions = EXCLUDED.source_positions,
            geom = EXCLUDED.geom,
            point_count = EXCLUDED.point_count,
            camera_anchor_geom = EXCLUDED.camera_anchor_geom,
            boundary_a_bearing = EXCLUDED.boundary_a_bearing,
            boundary_b_bearing = EXCLUDED.boundary_b_bearing,
            suggested_centre_bearing = EXCLUDED.suggested_centre_bearing,
            suggested_coverage_angle = EXCLUDED.suggested_coverage_angle,
            geometry_status = EXCLUDED.geometry_status,
            binding_state = EXCLUDED.binding_state,
            requires_review = EXCLUDED.requires_review,
            classification_confidence = EXCLUDED.classification_confidence,
            updated_at = NOW();
    """
    direction_params = []
    for direction in bundle.directions:
        analysis = direction.analysis
        anchor_lng = analysis.camera_anchor[0] if analysis else None
        anchor_lat = analysis.camera_anchor[1] if analysis else None
        direction_params.append(
            (
                raw_upload_id,
                direction.placemark_index,
                direction.name,
                direction.task_family,
                json.dumps(direction.positions),
                _linestring_wkt(direction.positions),
                len(direction.positions),
                anchor_lng,
                anchor_lat,
                analysis.boundary_a_bearing if analysis else None,
                analysis.boundary_b_bearing if analysis else None,
                analysis.suggested_centre_bearing if analysis else None,
                analysis.suggested_coverage_angle if analysis else None,
                direction.geometry_status,
                direction.binding_state,
                direction.requires_review,
                direction.classification_confidence,
                settings.OBSERVATION_NORMALIZATION_VERSION,
                settings.RAW_PLACEMARK_PARSER_VERSION,
                settings.CLASSIFICATION_RULE_VERSION,
            )
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            if camera_params:
                await conn.executemany(camera_query, camera_params)
            if direction_params:
                await conn.executemany(direction_query, direction_params)

            camera_rows = await conn.fetch(
                """
                SELECT rp.placemark_index, co.id
                FROM camera_observations co
                JOIN raw_placemarks rp ON rp.id = co.raw_placemark_id
                JOIN classification_decisions cd
                  ON cd.id = co.classification_decision_id
                WHERE rp.raw_upload_id = $1
                  AND rp.raw_parser_version = $2
                  AND cd.rule_version = $3
                  AND co.normalization_version = $4;
                """,
                raw_upload_id,
                settings.RAW_PLACEMARK_PARSER_VERSION,
                settings.CLASSIFICATION_RULE_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )
            direction_rows = await conn.fetch(
                """
                SELECT rp.placemark_index, dlo.id
                FROM direction_line_observations dlo
                JOIN raw_placemarks rp ON rp.id = dlo.raw_placemark_id
                JOIN classification_decisions cd
                  ON cd.id = dlo.classification_decision_id
                WHERE rp.raw_upload_id = $1
                  AND rp.raw_parser_version = $2
                  AND cd.rule_version = $3
                  AND dlo.normalization_version = $4;
                """,
                raw_upload_id,
                settings.RAW_PLACEMARK_PARSER_VERSION,
                settings.CLASSIFICATION_RULE_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )
            camera_ids = {row["placemark_index"]: row["id"] for row in camera_rows}
            direction_ids = {
                row["placemark_index"]: row["id"] for row in direction_rows
            }

            await conn.execute(
                """
                DELETE FROM camera_direction_bindings cdb
                USING direction_line_observations dlo, raw_placemarks rp
                WHERE cdb.direction_line_observation_id = dlo.id
                  AND dlo.raw_placemark_id = rp.id
                  AND rp.raw_upload_id = $1
                  AND cdb.binding_version = $2
                  AND cdb.binding_status IN ('automatic', 'review_candidate');
                """,
                raw_upload_id,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )

            binding_params = [
                (
                    camera_ids[binding.camera_placemark_index],
                    direction_ids[binding.direction_placemark_index],
                    binding.anchor_distance_m,
                    binding.binding_status,
                    binding.is_selected,
                    binding.binding_confidence,
                    json.dumps(binding.binding_reasons),
                    settings.OBSERVATION_NORMALIZATION_VERSION,
                )
                for binding in bundle.bindings
                if binding.camera_placemark_index in camera_ids
                and binding.direction_placemark_index in direction_ids
            ]
            if binding_params:
                await conn.executemany(
                    """
                    INSERT INTO camera_direction_bindings (
                        camera_observation_id,
                        direction_line_observation_id,
                        anchor_distance_m,
                        binding_status,
                        is_selected,
                        binding_confidence,
                        binding_reasons,
                        binding_version
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                    ON CONFLICT (
                        camera_observation_id,
                        direction_line_observation_id,
                        binding_version
                    )
                    DO UPDATE SET
                        anchor_distance_m = EXCLUDED.anchor_distance_m,
                        binding_status = EXCLUDED.binding_status,
                        is_selected = EXCLUDED.is_selected,
                        binding_confidence = EXCLUDED.binding_confidence,
                        binding_reasons = EXCLUDED.binding_reasons,
                        updated_at = NOW()
                    WHERE camera_direction_bindings.binding_status IN (
                        'automatic', 'review_candidate'
                    );
                    """,
                    binding_params,
                )

    return len(camera_rows), len(direction_rows), len(binding_params)


def _linestring_wkt(positions: tuple[tuple[float, ...], ...]) -> str:
    """Build a two-dimensional WKT LineString from preserved KML positions."""
    coordinates = ", ".join(f"{position[0]} {position[1]}" for position in positions)
    return f"LINESTRING ({coordinates})"


async def store_camera_site_relationship_bundle(
    raw_upload_id: UUID,
    bundle: CameraSiteRelationshipBundle,
) -> tuple[int, int, int]:
    """Persist Site observations and versioned primary/fallback suggestions."""
    pool = await get_pool()
    site_query = """
        INSERT INTO site_observations (
            raw_placemark_id,
            classification_decision_id,
            placemark_name,
            task_family,
            site_key,
            source_position,
            geom,
            required_camera_count,
            camera_count_source,
            classification_confidence,
            normalization_version
        )
        SELECT
            rp.id, cd.id, $3, $4, $5, $6::jsonb,
            ST_SetSRID(ST_MakePoint($7, $8), 4326), $9, $10, $11, $12
        FROM raw_placemarks rp
        JOIN classification_decisions cd ON cd.raw_placemark_id = rp.id
        WHERE rp.raw_upload_id = $1
          AND rp.placemark_index = $2
          AND rp.raw_parser_version = $13
          AND cd.rule_version = $14
        ON CONFLICT (classification_decision_id, normalization_version)
        DO UPDATE SET
            placemark_name = EXCLUDED.placemark_name,
            task_family = EXCLUDED.task_family,
            site_key = EXCLUDED.site_key,
            source_position = EXCLUDED.source_position,
            geom = EXCLUDED.geom,
            required_camera_count = EXCLUDED.required_camera_count,
            camera_count_source = EXCLUDED.camera_count_source,
            classification_confidence = EXCLUDED.classification_confidence,
            updated_at = NOW();
    """
    site_params = [
        (
            raw_upload_id,
            site.placemark_index,
            site.name,
            site.task_family,
            site.site_key,
            json.dumps(site.position),
            site.position[0],
            site.position[1],
            site.required_camera_count,
            site.camera_count_source,
            site.classification_confidence,
            settings.OBSERVATION_NORMALIZATION_VERSION,
            settings.RAW_PLACEMARK_PARSER_VERSION,
            settings.CLASSIFICATION_RULE_VERSION,
        )
        for site in bundle.sites
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            if site_params:
                await conn.executemany(site_query, site_params)

            camera_rows = await conn.fetch(
                """
                SELECT rp.placemark_index, co.id
                FROM camera_observations co
                JOIN raw_placemarks rp ON rp.id = co.raw_placemark_id
                JOIN classification_decisions cd
                  ON cd.id = co.classification_decision_id
                WHERE rp.raw_upload_id = $1
                  AND rp.raw_parser_version = $2
                  AND cd.rule_version = $3
                  AND co.normalization_version = $4;
                """,
                raw_upload_id,
                settings.RAW_PLACEMARK_PARSER_VERSION,
                settings.CLASSIFICATION_RULE_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )
            site_rows = await conn.fetch(
                """
                SELECT rp.placemark_index, so.id
                FROM site_observations so
                JOIN raw_placemarks rp ON rp.id = so.raw_placemark_id
                JOIN classification_decisions cd
                  ON cd.id = so.classification_decision_id
                WHERE rp.raw_upload_id = $1
                  AND rp.raw_parser_version = $2
                  AND cd.rule_version = $3
                  AND so.normalization_version = $4;
                """,
                raw_upload_id,
                settings.RAW_PLACEMARK_PARSER_VERSION,
                settings.CLASSIFICATION_RULE_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )
            camera_ids = {row["placemark_index"]: row["id"] for row in camera_rows}
            site_ids = {row["placemark_index"]: row["id"] for row in site_rows}

            await conn.execute(
                """
                DELETE FROM camera_site_relationships csr
                USING camera_observations co, raw_placemarks rp
                WHERE csr.camera_observation_id = co.id
                  AND co.raw_placemark_id = rp.id
                  AND rp.raw_upload_id = $1
                  AND csr.association_version = $2
                  AND csr.relationship_status = 'suggested';
                """,
                raw_upload_id,
                settings.CAMERA_SITE_RELATIONSHIP_VERSION,
            )
            await conn.execute(
                """
                DELETE FROM camera_site_review_items csri
                USING camera_observations co, raw_placemarks rp
                WHERE csri.camera_observation_id = co.id
                  AND co.raw_placemark_id = rp.id
                  AND rp.raw_upload_id = $1
                  AND csri.association_version = $2
                  AND csri.review_status = 'open';
                """,
                raw_upload_id,
                settings.CAMERA_SITE_RELATIONSHIP_VERSION,
            )

            relationship_params = [
                (
                    camera_ids[relationship.camera_placemark_index],
                    site_ids[relationship.site_placemark_index],
                    relationship.relationship_role,
                    relationship.distance_m,
                    relationship.association_method,
                    relationship.is_named_association,
                    relationship.relationship_confidence,
                    json.dumps(relationship.relationship_reasons),
                    settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                )
                for relationship in bundle.relationships
                if relationship.camera_placemark_index in camera_ids
                and relationship.site_placemark_index in site_ids
            ]
            if relationship_params:
                await conn.executemany(
                    """
                    INSERT INTO camera_site_relationships (
                        camera_observation_id,
                        site_observation_id,
                        relationship_role,
                        distance_m,
                        association_method,
                        is_named_association,
                        relationship_confidence,
                        relationship_reasons,
                        association_version
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
                    ON CONFLICT (
                        camera_observation_id,
                        site_observation_id,
                        association_version
                    )
                    DO UPDATE SET
                        relationship_role = EXCLUDED.relationship_role,
                        distance_m = EXCLUDED.distance_m,
                        association_method = EXCLUDED.association_method,
                        is_named_association = EXCLUDED.is_named_association,
                        relationship_confidence = EXCLUDED.relationship_confidence,
                        relationship_reasons = EXCLUDED.relationship_reasons,
                        updated_at = NOW()
                    WHERE camera_site_relationships.relationship_status = 'suggested';
                    """,
                    relationship_params,
                )

            review_params = [
                (
                    camera_ids[review.camera_placemark_index],
                    (
                        site_ids.get(review.candidate_site_placemark_index)
                        if review.candidate_site_placemark_index is not None
                        else None
                    ),
                    review.review_code,
                    review.candidate_distance_m,
                    json.dumps(review.named_site_references),
                    json.dumps(review.review_reasons),
                    settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                )
                for review in bundle.review_items
                if review.camera_placemark_index in camera_ids
            ]
            if review_params:
                await conn.executemany(
                    """
                    INSERT INTO camera_site_review_items (
                        camera_observation_id,
                        candidate_site_observation_id,
                        review_code,
                        candidate_distance_m,
                        named_site_references,
                        review_reasons,
                        association_version
                    )
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                    ON CONFLICT (
                        camera_observation_id,
                        review_code,
                        association_version
                    )
                    DO UPDATE SET
                        candidate_site_observation_id = EXCLUDED.candidate_site_observation_id,
                        candidate_distance_m = EXCLUDED.candidate_distance_m,
                        named_site_references = EXCLUDED.named_site_references,
                        review_reasons = EXCLUDED.review_reasons,
                        updated_at = NOW()
                    WHERE camera_site_review_items.review_status = 'open';
                    """,
                    review_params,
                )

    return len(site_rows), len(relationship_params), len(review_params)


async def store_polygon_scope_bundle(
    raw_upload_id: UUID,
    bundle: PolygonScopeBundle,
) -> tuple[int, int]:
    """Persist Polygon scope decisions and contained-Site memberships."""
    pool = await get_pool()
    polygon_query = """
        INSERT INTO site_scope_polygon_observations (
            raw_placemark_id,
            classification_decision_id,
            placemark_name,
            task_family,
            outer_ring,
            inner_rings,
            geom,
            distinct_outer_vertex_count,
            style_evidence,
            scope_status,
            requires_review,
            classification_reasons,
            scope_version
        )
        SELECT
            rp.id, cd.id, $3, $4, $5::jsonb, $6::jsonb,
            CASE WHEN $7::text IS NULL THEN NULL ELSE ST_GeomFromText($7, 4326) END,
            $8, $9::jsonb, $10, $11, $12::jsonb, $13
        FROM raw_placemarks rp
        JOIN classification_decisions cd ON cd.raw_placemark_id = rp.id
        WHERE rp.raw_upload_id = $1
          AND rp.placemark_index = $2
          AND rp.raw_parser_version = $14
          AND cd.rule_version = $15
        ON CONFLICT (classification_decision_id, scope_version)
        DO UPDATE SET
            placemark_name = EXCLUDED.placemark_name,
            task_family = EXCLUDED.task_family,
            outer_ring = EXCLUDED.outer_ring,
            inner_rings = EXCLUDED.inner_rings,
            geom = EXCLUDED.geom,
            distinct_outer_vertex_count = EXCLUDED.distinct_outer_vertex_count,
            style_evidence = EXCLUDED.style_evidence,
            scope_status = EXCLUDED.scope_status,
            requires_review = EXCLUDED.requires_review,
            classification_reasons = EXCLUDED.classification_reasons,
            updated_at = NOW()
        WHERE site_scope_polygon_observations.decision_status = 'suggested';
    """
    polygon_params = [
        (
            raw_upload_id,
            polygon.placemark_index,
            polygon.name,
            polygon.task_family,
            json.dumps(polygon.outer_ring),
            json.dumps(polygon.inner_rings),
            _polygon_wkt(polygon.outer_ring, polygon.inner_rings),
            polygon.distinct_outer_vertex_count,
            json.dumps(polygon.style_evidence),
            polygon.scope_status,
            polygon.requires_review,
            json.dumps(polygon.classification_reasons),
            settings.POLYGON_SCOPE_VERSION,
            settings.RAW_PLACEMARK_PARSER_VERSION,
            settings.CLASSIFICATION_RULE_VERSION,
        )
        for polygon in bundle.polygons
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            if polygon_params:
                await conn.executemany(polygon_query, polygon_params)
            polygon_rows = await conn.fetch(
                """
                SELECT rp.placemark_index, sspo.id
                FROM site_scope_polygon_observations sspo
                JOIN raw_placemarks rp ON rp.id = sspo.raw_placemark_id
                WHERE rp.raw_upload_id = $1
                  AND rp.raw_parser_version = $2
                  AND sspo.scope_version = $3;
                """,
                raw_upload_id,
                settings.RAW_PLACEMARK_PARSER_VERSION,
                settings.POLYGON_SCOPE_VERSION,
            )
            site_rows = await conn.fetch(
                """
                SELECT rp.placemark_index, so.id
                FROM site_observations so
                JOIN raw_placemarks rp ON rp.id = so.raw_placemark_id
                WHERE rp.raw_upload_id = $1
                  AND rp.raw_parser_version = $2
                  AND so.normalization_version = $3;
                """,
                raw_upload_id,
                settings.RAW_PLACEMARK_PARSER_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )
            polygon_ids = {
                row["placemark_index"]: row["id"] for row in polygon_rows
            }
            site_ids = {row["placemark_index"]: row["id"] for row in site_rows}
            await conn.execute(
                """
                DELETE FROM site_scope_polygon_memberships sspm
                USING site_scope_polygon_observations sspo, raw_placemarks rp
                WHERE sspm.polygon_observation_id = sspo.id
                  AND sspo.raw_placemark_id = rp.id
                  AND rp.raw_upload_id = $1
                  AND sspm.scope_version = $2
                  AND sspm.membership_status = 'suggested';
                """,
                raw_upload_id,
                settings.POLYGON_SCOPE_VERSION,
            )
            membership_params = [
                (
                    polygon_ids[membership.polygon_placemark_index],
                    site_ids[membership.site_placemark_index],
                    membership.membership_method,
                    membership.membership_confidence,
                    json.dumps(membership.membership_reasons),
                    settings.POLYGON_SCOPE_VERSION,
                )
                for membership in bundle.memberships
                if membership.polygon_placemark_index in polygon_ids
                and membership.site_placemark_index in site_ids
            ]
            if membership_params:
                await conn.executemany(
                    """
                    INSERT INTO site_scope_polygon_memberships (
                        polygon_observation_id,
                        site_observation_id,
                        membership_method,
                        membership_confidence,
                        membership_reasons,
                        scope_version
                    )
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                    ON CONFLICT (
                        polygon_observation_id,
                        site_observation_id,
                        scope_version
                    )
                    DO UPDATE SET
                        membership_method = EXCLUDED.membership_method,
                        membership_confidence = EXCLUDED.membership_confidence,
                        membership_reasons = EXCLUDED.membership_reasons,
                        updated_at = NOW()
                    WHERE site_scope_polygon_memberships.membership_status = 'suggested';
                    """,
                    membership_params,
                )
    return len(polygon_rows), len(membership_params)


def _polygon_wkt(
    outer_ring: tuple[tuple[float, ...], ...],
    inner_rings: tuple[tuple[tuple[float, ...], ...], ...],
) -> str | None:
    if len(outer_ring) < 4 or outer_ring[0][:2] != outer_ring[-1][:2]:
        return None
    rings = (outer_ring, *inner_rings)
    rendered = [
        "(" + ", ".join(f"{position[0]} {position[1]}" for position in ring) + ")"
        for ring in rings
    ]
    return "POLYGON (" + ", ".join(rendered) + ")"


async def rebuild_canonical_sites() -> tuple[int, int, int]:
    """Rebuild non-chain Canonical Sites and Polygon scope memberships."""
    pool = await get_pool()
    canonical_version = settings.CANONICAL_SITE_VERSION
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1));",
                canonical_version,
            )
            await conn.execute(
                """
                DELETE FROM canonical_sites
                WHERE canonical_version = $1
                  AND canonical_status = 'suggested';
                """,
                canonical_version,
            )
            rows = await conn.fetch(
                """
                SELECT
                    so.id,
                    rp.raw_upload_id,
                    ST_X(so.geom) AS lng,
                    ST_Y(so.geom) AS lat,
                    so.task_family
                FROM site_observations so
                JOIN raw_placemarks rp ON rp.id = so.raw_placemark_id
                WHERE so.normalization_version = $1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM canonical_site_memberships csm
                      JOIN canonical_sites cs ON cs.id = csm.canonical_site_id
                      WHERE csm.site_observation_id = so.id
                        AND csm.canonical_version = $2
                        AND cs.canonical_status IN (
                            'human_confirmed', 'human_rejected'
                        )
                  )
                ORDER BY so.id;
                """,
                settings.OBSERVATION_NORMALIZATION_VERSION,
                canonical_version,
            )
            evidence = [
                CanonicalSiteEvidence(
                    site_observation_id=str(row["id"]),
                    raw_upload_id=str(row["raw_upload_id"]),
                    position=(float(row["lng"]), float(row["lat"])),
                    task_family=row["task_family"],
                )
                for row in rows
            ]
            candidates = build_canonical_sites(
                evidence,
                settings.SITE_EQUIVALENCE_RADIUS_M,
                canonical_version,
            )
            stored_sites = 0
            stored_members = 0
            for candidate in candidates:
                canonical_site_id = await conn.fetchval(
                    """
                    INSERT INTO canonical_sites (
                        canonical_key,
                        representative_site_observation_id,
                        representative_geom,
                        task_family,
                        member_count,
                        diameter_m,
                        matching_method,
                        match_confidence,
                        match_reasons,
                        canonical_version
                    )
                    VALUES (
                        $1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326),
                        $5, $6, $7, 'complete_link', $8, $9::jsonb, $10
                    )
                    ON CONFLICT (canonical_key, canonical_version) DO NOTHING
                    RETURNING id;
                    """,
                    candidate.canonical_key,
                    UUID(candidate.representative_site_observation_id),
                    candidate.representative_position[0],
                    candidate.representative_position[1],
                    candidate.task_family,
                    len(candidate.members),
                    candidate.diameter_m,
                    candidate.match_confidence,
                    json.dumps(candidate.match_reasons),
                    canonical_version,
                )
                if canonical_site_id is None:
                    continue
                stored_sites += 1
                member_params = [
                    (
                        canonical_site_id,
                        UUID(member.site_observation_id),
                        haversine_metres(
                            member.position,
                            candidate.representative_position,
                        ),
                        (
                            member.site_observation_id
                            == candidate.representative_site_observation_id
                        ),
                        canonical_version,
                    )
                    for member in candidate.members
                ]
                await conn.executemany(
                    """
                    INSERT INTO canonical_site_memberships (
                        canonical_site_id,
                        site_observation_id,
                        distance_to_representative_m,
                        is_representative,
                        canonical_version
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (site_observation_id, canonical_version)
                    DO NOTHING;
                    """,
                    member_params,
                )
                stored_members += len(member_params)

            await conn.execute(
                """
                DELETE FROM canonical_site_scope_memberships
                WHERE canonical_version = $1
                  AND scope_version = $2
                  AND membership_status = 'suggested';
                """,
                canonical_version,
                settings.POLYGON_SCOPE_VERSION,
            )
            await conn.execute(
                """
                INSERT INTO canonical_site_scope_memberships (
                    polygon_observation_id,
                    canonical_site_id,
                    membership_method,
                    canonical_version,
                    scope_version
                )
                SELECT DISTINCT
                    sspm.polygon_observation_id,
                    csm.canonical_site_id,
                    'derived_point_in_polygon',
                    $1,
                    $2
                FROM site_scope_polygon_memberships sspm
                JOIN canonical_site_memberships csm
                  ON csm.site_observation_id = sspm.site_observation_id
                 AND csm.canonical_version = $1
                 AND csm.membership_status IN (
                     'suggested', 'human_confirmed'
                 )
                JOIN canonical_sites cs
                  ON cs.id = csm.canonical_site_id
                 AND cs.canonical_status IN ('suggested', 'human_confirmed')
                WHERE sspm.scope_version = $2
                  AND sspm.membership_status IN (
                      'suggested', 'human_confirmed'
                  )
                ON CONFLICT (
                    polygon_observation_id,
                    canonical_site_id,
                    canonical_version,
                    scope_version
                ) DO NOTHING;
                """,
                canonical_version,
                settings.POLYGON_SCOPE_VERSION,
            )
            stored_scope_memberships = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM canonical_site_scope_memberships
                WHERE canonical_version = $1
                  AND scope_version = $2;
                """,
                canonical_version,
                settings.POLYGON_SCOPE_VERSION,
            )
    return stored_sites, stored_members, int(stored_scope_memberships)


async def rebuild_camera_location_clusters() -> tuple[int, int]:
    """Rebuild current rule-generated complete-link Camera clusters."""
    pool = await get_pool()
    cluster_version = settings.CAMERA_LOCATION_CLUSTER_VERSION
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1));",
                cluster_version,
            )
            await conn.execute(
                """
                DELETE FROM camera_location_clusters
                WHERE cluster_version = $1
                  AND cluster_status = 'suggested';
                """,
                cluster_version,
            )
            rows = await conn.fetch(
                """
                SELECT
                    co.id,
                    ST_X(co.geom) AS lng,
                    ST_Y(co.geom) AS lat
                FROM camera_observations co
                WHERE co.normalization_version = $1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM camera_location_cluster_members clcm
                      JOIN camera_location_clusters clc
                        ON clc.id = clcm.camera_location_cluster_id
                      WHERE clcm.camera_observation_id = co.id
                        AND clcm.cluster_version = $2
                        AND clc.cluster_status IN (
                            'human_confirmed', 'human_rejected'
                        )
                  )
                ORDER BY co.id;
                """,
                settings.OBSERVATION_NORMALIZATION_VERSION,
                cluster_version,
            )
            evidence = [
                CameraLocationEvidence(
                    evidence_id=str(row["id"]),
                    position=(float(row["lng"]), float(row["lat"])),
                )
                for row in rows
            ]
            clusters = build_camera_location_clusters(
                evidence,
                settings.CAMERA_LOCATION_CLUSTER_MAX_DIAMETER_M,
                cluster_version,
            )

            stored_clusters = 0
            stored_members = 0
            for cluster in clusters:
                cluster_id = await conn.fetchval(
                    """
                    INSERT INTO camera_location_clusters (
                        cluster_key,
                        representative_camera_observation_id,
                        representative_geom,
                        member_count,
                        diameter_m,
                        clustering_method,
                        cluster_version
                    )
                    VALUES (
                        $1, $2,
                        ST_SetSRID(ST_MakePoint($3, $4), 4326),
                        $5, $6, 'complete_link', $7
                    )
                    ON CONFLICT (cluster_key, cluster_version) DO NOTHING
                    RETURNING id;
                    """,
                    cluster.cluster_key,
                    UUID(cluster.representative_evidence_id),
                    cluster.representative_position[0],
                    cluster.representative_position[1],
                    len(cluster.members),
                    cluster.diameter_m,
                    cluster_version,
                )
                if cluster_id is None:
                    continue
                stored_clusters += 1
                member_params = [
                    (
                        cluster_id,
                        UUID(member.evidence_id),
                        haversine_metres(
                            member.position,
                            cluster.representative_position,
                        ),
                        member.evidence_id == cluster.representative_evidence_id,
                        cluster_version,
                    )
                    for member in cluster.members
                ]
                await conn.executemany(
                    """
                    INSERT INTO camera_location_cluster_members (
                        camera_location_cluster_id,
                        camera_observation_id,
                        distance_to_representative_m,
                        is_representative,
                        cluster_version
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (camera_observation_id, cluster_version)
                    DO NOTHING;
                    """,
                    member_params,
                )
                stored_members += len(member_params)

    return stored_clusters, stored_members


async def rebuild_site_camera_count_decisions() -> int:
    """Rebuild explicit-first, historical-mode Camera count decisions."""
    pool = await get_pool()
    decision_version = settings.CAMERA_COUNT_DECISION_VERSION
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1));",
                decision_version,
            )
            rows = await conn.fetch(
                """
                SELECT
                    so.id,
                    rp.raw_upload_id,
                    ST_X(so.geom) AS lng,
                    ST_Y(so.geom) AS lat,
                    so.task_family,
                    so.required_camera_count,
                    so.camera_count_source,
                    csm.canonical_site_id,
                    EXTRACT(EPOCH FROM ru.created_at) AS observed_order,
                    COALESCE(
                        array_agg(
                            DISTINCT clcm.camera_location_cluster_id::text
                        ) FILTER (
                            WHERE clcm.camera_location_cluster_id IS NOT NULL
                              AND clc.cluster_status IN (
                                  'suggested', 'human_confirmed'
                              )
                        ),
                        ARRAY[]::text[]
                    ) AS primary_camera_location_ids,
                    COALESCE(
                        jsonb_agg(
                            DISTINCT jsonb_build_object(
                                'camera_observation_id', co.id::text,
                                'cluster_id', clcm.camera_location_cluster_id::text,
                                'name', co.placemark_name,
                                'description', camera_rp.description,
                                'direction_count', COALESCE(direction_counts.direction_count, 0)
                            )
                        ) FILTER (
                            WHERE co.id IS NOT NULL
                              AND clcm.camera_location_cluster_id IS NOT NULL
                              AND clc.cluster_status IN (
                                  'suggested', 'human_confirmed'
                              )
                        ),
                        '[]'::jsonb
                    ) AS primary_camera_evidence
                FROM site_observations so
                JOIN raw_placemarks rp ON rp.id = so.raw_placemark_id
                JOIN raw_uploads ru ON ru.id = rp.raw_upload_id
                JOIN canonical_site_memberships csm
                  ON csm.site_observation_id = so.id
                 AND csm.canonical_version = $4
                 AND csm.membership_status IN (
                     'suggested', 'human_confirmed'
                 )
                LEFT JOIN camera_site_relationships csr
                  ON csr.site_observation_id = so.id
                 AND csr.association_version = $2
                 AND csr.relationship_role = 'primary'
                 AND csr.relationship_status IN (
                     'suggested', 'human_confirmed'
                 )
                LEFT JOIN camera_observations co
                  ON co.id = csr.camera_observation_id
                LEFT JOIN raw_placemarks camera_rp
                  ON camera_rp.id = co.raw_placemark_id
                LEFT JOIN camera_location_cluster_members clcm
                  ON clcm.camera_observation_id = csr.camera_observation_id
                 AND clcm.cluster_version = $3
                LEFT JOIN camera_location_clusters clc
                  ON clc.id = clcm.camera_location_cluster_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(DISTINCT cdb.direction_line_observation_id) AS direction_count
                    FROM camera_direction_bindings cdb
                    WHERE cdb.camera_observation_id = co.id
                      AND cdb.binding_version = $1
                      AND cdb.is_selected
                      AND cdb.binding_status IN ('automatic', 'human_confirmed')
                ) direction_counts ON TRUE
                WHERE so.normalization_version = $1
                GROUP BY
                    so.id,
                    rp.raw_upload_id,
                    so.geom,
                    so.task_family,
                    so.required_camera_count,
                    so.camera_count_source,
                    csm.canonical_site_id,
                    ru.created_at
                ORDER BY so.id;
                """,
                settings.OBSERVATION_NORMALIZATION_VERSION,
                settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                settings.CAMERA_LOCATION_CLUSTER_VERSION,
                settings.CANONICAL_SITE_VERSION,
            )
            evidence = [
                SiteCameraCountEvidence(
                    site_observation_id=str(row["id"]),
                    raw_upload_id=str(row["raw_upload_id"]),
                    position=(float(row["lng"]), float(row["lat"])),
                    task_family=row["task_family"],
                    required_camera_count=row["required_camera_count"],
                    camera_count_source=row["camera_count_source"],
                    primary_camera_location_ids=tuple(
                        row["primary_camera_location_ids"]
                    ),
                    primary_camera_count=_primary_camera_count_from_evidence(
                        row["primary_camera_evidence"]
                    ),
                    observed_order=float(row["observed_order"]),
                    canonical_site_id=str(row["canonical_site_id"]),
                )
                for row in rows
            ]
            decisions = [
                select_site_camera_count(
                    target,
                    evidence,
                    settings.SITE_EQUIVALENCE_RADIUS_M,
                )
                for target in evidence
            ]

            await conn.execute(
                """
                DELETE FROM site_camera_count_decisions
                WHERE decision_version = $1
                  AND decision_status = 'suggested';
                """,
                decision_version,
            )
            params = [
                (
                    UUID(decision.site_observation_id),
                    decision.required_camera_count,
                    decision.camera_count_source,
                    decision.decision_method,
                    decision.decision_confidence,
                    json.dumps(decision.decision_reasons),
                    json.dumps(decision.supporting_site_observation_ids),
                    json.dumps(decision.historical_count_votes),
                    decision.requires_review,
                    decision_version,
                )
                for decision in decisions
            ]
            if params:
                await conn.executemany(
                    """
                    INSERT INTO site_camera_count_decisions (
                        site_observation_id,
                        required_camera_count,
                        camera_count_source,
                        decision_method,
                        decision_confidence,
                        decision_reasons,
                        supporting_site_observation_ids,
                        historical_count_votes,
                        requires_review,
                        decision_version
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6::jsonb,
                        $7::jsonb, $8::jsonb, $9, $10
                    )
                    ON CONFLICT (site_observation_id, decision_version)
                    DO UPDATE SET
                        required_camera_count = EXCLUDED.required_camera_count,
                        camera_count_source = EXCLUDED.camera_count_source,
                        decision_method = EXCLUDED.decision_method,
                        decision_confidence = EXCLUDED.decision_confidence,
                        decision_reasons = EXCLUDED.decision_reasons,
                        supporting_site_observation_ids = EXCLUDED.supporting_site_observation_ids,
                        historical_count_votes = EXCLUDED.historical_count_votes,
                        requires_review = EXCLUDED.requires_review,
                        updated_at = NOW()
                    WHERE site_camera_count_decisions.decision_status = 'suggested';
                    """,
                    params,
                )
    return len(decisions)


def _primary_camera_count_from_evidence(value) -> int:
    if not value:
        return 0
    if isinstance(value, str):
        value = json.loads(value)

    total = 0
    seen: set[tuple[str | None, str | None]] = set()
    for item in value:
        cluster_id = item.get("cluster_id")
        camera_id = item.get("camera_observation_id")
        dedupe_key = (cluster_id, camera_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        total += infer_camera_quantity(
            item.get("name") or "",
            item.get("description") or "",
            int(item.get("direction_count") or 0),
        )
    return total


async def rebuild_site_camera_generation_results() -> tuple[int, int]:
    """Rebuild tiered, Cluster-de-duplicated Camera generation suggestions."""
    pool = await get_pool()
    generation_version = settings.CAMERA_GENERATION_VERSION
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1));",
                generation_version,
            )
            protected_site_ids = set(
                await conn.fetchval(
                    """
                    SELECT COALESCE(array_agg(site_observation_id), ARRAY[]::uuid[])
                    FROM site_camera_generation_results
                    WHERE generation_version = $1
                      AND result_status IN ('human_confirmed', 'human_rejected');
                    """,
                    generation_version,
                )
            )
            await conn.execute(
                """
                DELETE FROM site_camera_generation_results
                WHERE generation_version = $1
                  AND result_status = 'suggested';
                """,
                generation_version,
            )
            site_rows = await conn.fetch(
                """
                SELECT
                    so.id AS site_observation_id,
                    scd.id AS camera_count_decision_id,
                    scd.required_camera_count,
                    so.task_family,
                    ST_X(so.geom) AS site_lng,
                    ST_Y(so.geom) AS site_lat
                FROM site_observations so
                JOIN site_camera_count_decisions scd
                  ON scd.site_observation_id = so.id
                 AND scd.decision_version = $1
                 AND scd.decision_status IN ('suggested', 'human_confirmed')
                WHERE so.normalization_version = $2
                ORDER BY so.id;
                """,
                settings.CAMERA_COUNT_DECISION_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )
            relationship_rows = await conn.fetch(
                """
                SELECT
                    csr.id AS relationship_id,
                    csr.site_observation_id,
                    csr.relationship_role,
                    csr.distance_m,
                    csr.relationship_confidence,
                    csr.relationship_status,
                    clc.id AS cluster_id,
                    clc.representative_camera_observation_id,
                    clc.member_count,
                    clc.cluster_status,
                    relationship_camera.placemark_name AS evidence_camera_name,
                    relationship_rp.description AS evidence_camera_description,
                    COALESCE(direction_counts.direction_count, 0) AS evidence_direction_count
                FROM camera_site_relationships csr
                JOIN camera_observations relationship_camera
                  ON relationship_camera.id = csr.camera_observation_id
                JOIN raw_placemarks relationship_rp
                  ON relationship_rp.id = relationship_camera.raw_placemark_id
                JOIN camera_location_cluster_members clcm
                  ON clcm.camera_observation_id = csr.camera_observation_id
                 AND clcm.cluster_version = $2
                JOIN camera_location_clusters clc
                  ON clc.id = clcm.camera_location_cluster_id
                 AND clc.cluster_status IN ('suggested', 'human_confirmed')
                JOIN camera_observations representative_camera
                  ON representative_camera.id = clc.representative_camera_observation_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(DISTINCT cdb.direction_line_observation_id) AS direction_count
                    FROM camera_direction_bindings cdb
                    WHERE cdb.camera_observation_id = csr.camera_observation_id
                      AND cdb.binding_version = $3
                      AND cdb.is_selected
                      AND cdb.binding_status IN ('automatic', 'human_confirmed')
                ) direction_counts ON TRUE
                WHERE csr.association_version = $1
                  AND csr.relationship_status IN ('suggested', 'human_confirmed')
                ORDER BY csr.site_observation_id, csr.id;
                """,
                settings.CAMERA_SITE_RELATIONSHIP_VERSION,
                settings.CAMERA_LOCATION_CLUSTER_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )
            cluster_rows = await conn.fetch(
                """
                SELECT
                    clc.id,
                    clc.representative_camera_observation_id,
                    representative_camera.task_family,
                    ST_X(clc.representative_geom) AS lng,
                    ST_Y(clc.representative_geom) AS lat,
                    clc.member_count,
                    clc.cluster_status,
                    representative_camera.placemark_name AS evidence_camera_name,
                    representative_rp.description AS evidence_camera_description,
                    COALESCE(direction_counts.direction_count, 0) AS evidence_direction_count
                FROM camera_location_clusters clc
                JOIN camera_observations representative_camera
                  ON representative_camera.id = clc.representative_camera_observation_id
                JOIN raw_placemarks representative_rp
                  ON representative_rp.id = representative_camera.raw_placemark_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(DISTINCT cdb.direction_line_observation_id) AS direction_count
                    FROM camera_direction_bindings cdb
                    WHERE cdb.camera_observation_id = representative_camera.id
                      AND cdb.binding_version = $2
                      AND cdb.is_selected
                      AND cdb.binding_status IN ('automatic', 'human_confirmed')
                ) direction_counts ON TRUE
                WHERE clc.cluster_version = $1
                  AND clc.cluster_status IN ('suggested', 'human_confirmed')
                ORDER BY clc.id;
                """,
                settings.CAMERA_LOCATION_CLUSTER_VERSION,
                settings.OBSERVATION_NORMALIZATION_VERSION,
            )

            relationships_by_site: dict[UUID, list[asyncpg.Record]] = {}
            for row in relationship_rows:
                relationships_by_site.setdefault(
                    row["site_observation_id"],
                    [],
                ).append(row)
            stored_results = 0
            stored_selections = 0
            for site in site_rows:
                site_id = site["site_observation_id"]
                if site_id in protected_site_ids:
                    continue
                site_position = (float(site["site_lng"]), float(site["site_lat"]))
                candidates = [
                    CameraGenerationCandidate(
                        camera_location_cluster_id=str(row["cluster_id"]),
                        representative_camera_observation_id=str(
                            row["representative_camera_observation_id"]
                        ),
                        selection_tier=row["relationship_role"],
                        distance_to_site_m=float(row["distance_m"]),
                        candidate_confidence=float(
                            row["relationship_confidence"]
                        ),
                        source_relationship_id=str(row["relationship_id"]),
                        historical_observation_count=int(row["member_count"]),
                        relationship_status=row["relationship_status"],
                        cluster_status=row["cluster_status"],
                        represented_camera_count=infer_camera_quantity(
                            row["evidence_camera_name"] or "",
                            row["evidence_camera_description"] or "",
                            int(row["evidence_direction_count"] or 0),
                        ),
                    )
                    for row in relationships_by_site.get(site_id, [])
                ]
                for cluster in cluster_rows:
                    cluster_position = (
                        float(cluster["lng"]),
                        float(cluster["lat"]),
                    )
                    distance = haversine_metres(site_position, cluster_position)
                    if not task_families_compatible(
                        site["task_family"],
                        cluster["task_family"],
                    ):
                        continue
                    if distance > settings.NEARBY_HISTORICAL_CAMERA_RADIUS_M:
                        continue
                    candidates.append(
                        CameraGenerationCandidate(
                            camera_location_cluster_id=str(cluster["id"]),
                            representative_camera_observation_id=str(
                                cluster[
                                    "representative_camera_observation_id"
                                ]
                            ),
                            selection_tier="nearby_historical",
                            distance_to_site_m=distance,
                            candidate_confidence=max(
                                0.25,
                                0.55 - distance / 200.0,
                            ),
                            historical_observation_count=int(
                                cluster["member_count"]
                            ),
                            cluster_status=cluster["cluster_status"],
                            represented_camera_count=infer_camera_quantity(
                                cluster["evidence_camera_name"] or "",
                                cluster["evidence_camera_description"] or "",
                                int(cluster["evidence_direction_count"] or 0),
                            ),
                        )
                    )
                result = select_camera_locations(
                    str(site_id),
                    site["required_camera_count"],
                    candidates,
                )
                result_id = await conn.fetchval(
                    """
                    INSERT INTO site_camera_generation_results (
                        site_observation_id,
                        camera_count_decision_id,
                        required_camera_count,
                        selected_camera_count,
                        missing_reliable_location_count,
                        generation_status,
                        site_description_note,
                        generation_reasons,
                        requires_review,
                        generation_version
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10
                    )
                    ON CONFLICT (site_observation_id, generation_version)
                    DO NOTHING
                    RETURNING id;
                    """,
                    site_id,
                    site["camera_count_decision_id"],
                    result.required_camera_count,
                    result.selected_camera_count,
                    result.missing_reliable_location_count,
                    result.generation_status,
                    result.site_description_note,
                    json.dumps(result.generation_reasons),
                    result.requires_review,
                    generation_version,
                )
                if result_id is None:
                    continue
                stored_results += 1
                selection_params = [
                    (
                        result_id,
                        UUID(selection.camera_location_cluster_id),
                        UUID(selection.representative_camera_observation_id),
                        (
                            UUID(selection.source_relationship_id)
                            if selection.source_relationship_id
                            else None
                        ),
                        selection.selection_tier,
                        selection.selection_rank,
                        selection.distance_to_site_m,
                        selection.candidate_confidence,
                        selection.ranking_score,
                        selection.ranking_method,
                        json.dumps(selection.ranking_reasons),
                        generation_version,
                    )
                    for selection in result.selected_locations
                ]
                if selection_params:
                    await conn.executemany(
                        """
                        INSERT INTO site_camera_generation_selections (
                            generation_result_id,
                            camera_location_cluster_id,
                            representative_camera_observation_id,
                            source_relationship_id,
                            selection_tier,
                            selection_rank,
                            distance_to_site_m,
                            candidate_confidence,
                            ranking_score,
                            ranking_method,
                            ranking_reasons,
                            generation_version
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12);
                        """,
                        selection_params,
                    )
                    stored_selections += len(selection_params)

    return stored_results, stored_selections


async def fetch_clustered_points(
    lat: float,
    lng: float,
    radius_m: float | None = None,
) -> list[asyncpg.Record]:
    """Call ``fn_get_clustered_reference_points`` and return raw records.

    Uses the application-wide settings for radius, DBSCAN eps, and heading bins.
    """
    pool = await get_pool()
    projected_srid = get_projected_srid(lng)
    query = """
        SELECT
            representative_lat,
            representative_lng,
            latest_azimuth,
            equipment_type,
            layout_family,
            source_group,
            total_usage,
            time_decay_score,
            record_count,
            distance_m,
            latest_timestamp
        FROM fn_get_clustered_reference_points(
            p_lat          := $1,
            p_lng          := $2,
            p_radius_m     := $3,
            p_eps_m        := $4,
            p_heading_bin_deg := $5,
            p_projected_srid := $6,
            p_layout_family := $7,
            p_parser_version := $8,
            p_site_equivalence_m := $9
        );
    """
    rows: list[asyncpg.Record] = await pool.fetch(
        query,
        lat,
        lng,
        radius_m if radius_m is not None else settings.FADE_RADIUS_M,  # search out to the fade boundary
        settings.DBSCAN_EPS_METERS,
        settings.HEADING_BIN_DEG,
        projected_srid,
        "ic",
        settings.HISTORY_PARSER_VERSION,
        settings.SITE_EQUIVALENCE_RADIUS_M,
    )
    logger.info(
        "Fetched %d clustered points for (%.6f, %.6f) using SRID %d.",
        len(rows),
        lat,
        lng,
        projected_srid,
    )
    return rows


async def match_canonical_site_for_point(
    lat: float,
    lng: float,
    radius_m: float,
    task_family: str = "unknown",
) -> asyncpg.Record | None:
    """Return the nearest accepted Canonical Site using all member Points."""
    pool = await get_pool()
    return await pool.fetchrow(
        """
        WITH request_point AS (
            SELECT ST_SetSRID(ST_MakePoint($1, $2), 4326) AS geom
        )
        SELECT
            cs.id AS canonical_site_id,
            representative.placemark_name AS canonical_name,
            ST_X(cs.representative_geom) AS lng,
            ST_Y(cs.representative_geom) AS lat,
            cs.task_family,
            MIN(ST_Distance(so.geom::geography, request_point.geom::geography))
                AS match_distance_m
        FROM canonical_sites cs
        JOIN site_observations representative
          ON representative.id = cs.representative_site_observation_id
        JOIN canonical_site_memberships csm
          ON csm.canonical_site_id = cs.id
         AND csm.canonical_version = $4
         AND csm.membership_status IN ('suggested', 'human_confirmed')
        JOIN site_observations so ON so.id = csm.site_observation_id
        CROSS JOIN request_point
        WHERE cs.canonical_version = $4
          AND cs.canonical_status IN ('suggested', 'human_confirmed')
          AND (
              $5 = 'unknown'
              OR cs.task_family IN ('unknown', 'mixed')
              OR ($5 IN ('od', 'ipod') AND cs.task_family IN ('od', 'ipod'))
              OR cs.task_family = $5
          )
          AND ST_DWithin(
              so.geom::geography,
              request_point.geom::geography,
              $3
          )
        GROUP BY cs.id, representative.placemark_name
        ORDER BY match_distance_m, cs.id
        LIMIT 1;
        """,
        lng,
        lat,
        radius_m,
        settings.CANONICAL_SITE_VERSION,
        task_family,
    )


async def match_canonical_sites_for_polygon(
    outer_ring: tuple[tuple[float, ...], ...],
    inner_rings: tuple[tuple[tuple[float, ...], ...], ...],
) -> list[asyncpg.Record]:
    """Return the contained Canonical Site nearest the Polygon centre for MVP."""
    polygon_wkt = _polygon_wkt(outer_ring, inner_rings)
    if polygon_wkt is None:
        return []
    pool = await get_pool()
    return await pool.fetch(
        """
        WITH request_polygon AS (
            SELECT ST_GeomFromText($1, 4326) AS geom
        )
        SELECT
            cs.id AS canonical_site_id,
            representative.placemark_name AS canonical_name,
            ST_X(cs.representative_geom) AS lng,
            ST_Y(cs.representative_geom) AS lat,
            cs.task_family,
            ST_Distance(
                cs.representative_geom::geography,
                ST_Centroid(request_polygon.geom)::geography
            ) AS match_distance_m
        FROM canonical_sites cs
        JOIN site_observations representative
          ON representative.id = cs.representative_site_observation_id
        JOIN canonical_site_memberships csm
          ON csm.canonical_site_id = cs.id
         AND csm.canonical_version = $2
         AND csm.membership_status IN ('suggested', 'human_confirmed')
        JOIN site_observations so ON so.id = csm.site_observation_id
        CROSS JOIN request_polygon
        WHERE cs.canonical_version = $2
          AND cs.canonical_status IN ('suggested', 'human_confirmed')
          AND ST_Covers(request_polygon.geom, so.geom)
        GROUP BY cs.id, representative.placemark_name, request_polygon.geom
        ORDER BY match_distance_m, cs.id
        LIMIT 1;
        """,
        polygon_wkt,
        settings.CANONICAL_SITE_VERSION,
    )


async def fetch_canonical_count_decision_rows(
    canonical_site_id: UUID,
) -> list[asyncpg.Record]:
    """Return accepted member-level count decisions for request-time selection."""
    pool = await get_pool()
    return await pool.fetch(
        """
        SELECT
            scd.required_camera_count,
            scd.camera_count_source,
            scd.decision_confidence,
            ru.created_at,
            so.id AS site_observation_id
        FROM canonical_site_memberships csm
        JOIN site_observations so ON so.id = csm.site_observation_id
        JOIN site_camera_count_decisions scd
          ON scd.site_observation_id = so.id
         AND scd.decision_version = $2
         AND scd.decision_status IN ('suggested', 'human_confirmed')
        JOIN raw_placemarks rp ON rp.id = so.raw_placemark_id
        JOIN raw_uploads ru ON ru.id = rp.raw_upload_id
        WHERE csm.canonical_site_id = $1
          AND csm.canonical_version = $3
          AND csm.membership_status IN ('suggested', 'human_confirmed')
          AND scd.required_camera_count IS NOT NULL
        ORDER BY ru.created_at DESC, so.id;
        """,
        canonical_site_id,
        settings.CAMERA_COUNT_DECISION_VERSION,
        settings.CANONICAL_SITE_VERSION,
    )


async def fetch_canonical_camera_candidate_rows(
    canonical_site_id: UUID,
) -> list[asyncpg.Record]:
    """Return relationship and nearby Cluster candidates for one Canonical Site."""
    pool = await get_pool()
    return await pool.fetch(
        """
        WITH canonical AS (
            SELECT id, representative_geom, task_family
            FROM canonical_sites
            WHERE id = $1
              AND canonical_version = $2
              AND canonical_status IN ('suggested', 'human_confirmed')
        ),
        related AS (
            SELECT
                clc.id AS cluster_id,
                clc.representative_camera_observation_id,
                camera.placemark_name AS camera_name,
                evidence_camera.placemark_name AS evidence_camera_name,
                evidence_rp.description AS evidence_camera_description,
                ST_X(clc.representative_geom) AS lng,
                ST_Y(clc.representative_geom) AS lat,
                clc.member_count,
                csr.relationship_role AS selection_tier,
                csr.relationship_confidence AS candidate_confidence,
                csr.relationship_status,
                clc.cluster_status,
                csr.id AS relationship_id,
                csm.site_observation_id AS layout_site_observation_id,
                site_rp.raw_upload_id AS layout_raw_upload_id,
                site_ru.source_file AS layout_source_file,
                site_ru.created_at AS layout_uploaded_at,
                ST_Distance(
                    clc.representative_geom::geography,
                    canonical.representative_geom::geography
                ) AS distance_to_site_m,
                direction.suggested_centre_bearing AS azimuth,
                direction.suggested_coverage_angle,
                direction.source_positions::text AS direction_positions_json,
                COALESCE(direction_counts.direction_count, 0) AS evidence_direction_count,
                ru.created_at AS latest_timestamp
            FROM canonical
            JOIN canonical_site_memberships csm
              ON csm.canonical_site_id = canonical.id
             AND csm.canonical_version = $2
             AND csm.membership_status IN ('suggested', 'human_confirmed')
            JOIN camera_site_relationships csr
              ON csr.site_observation_id = csm.site_observation_id
             AND csr.association_version = $3
             AND csr.relationship_status IN ('suggested', 'human_confirmed')
            JOIN site_observations layout_site
              ON layout_site.id = csm.site_observation_id
            JOIN raw_placemarks site_rp
              ON site_rp.id = layout_site.raw_placemark_id
            JOIN raw_uploads site_ru
              ON site_ru.id = site_rp.raw_upload_id
            JOIN camera_location_cluster_members clcm
              ON clcm.camera_observation_id = csr.camera_observation_id
             AND clcm.cluster_version = $4
            JOIN camera_location_clusters clc
              ON clc.id = clcm.camera_location_cluster_id
             AND clc.cluster_status IN ('suggested', 'human_confirmed')
            JOIN camera_observations camera
              ON camera.id = clc.representative_camera_observation_id
            JOIN camera_observations evidence_camera
              ON evidence_camera.id = csr.camera_observation_id
            JOIN raw_placemarks evidence_rp
              ON evidence_rp.id = evidence_camera.raw_placemark_id
            JOIN classification_decisions cd
              ON cd.id = camera.classification_decision_id
            JOIN raw_placemarks rp ON rp.id = cd.raw_placemark_id
            JOIN raw_uploads ru ON ru.id = rp.raw_upload_id
            LEFT JOIN LATERAL (
                SELECT
                    dlo.suggested_centre_bearing,
                    dlo.suggested_coverage_angle,
                    dlo.source_positions
                FROM camera_location_cluster_members direction_member
                JOIN camera_direction_bindings cdb
                  ON cdb.camera_observation_id = direction_member.camera_observation_id
                JOIN direction_line_observations dlo
                  ON dlo.id = cdb.direction_line_observation_id
                WHERE direction_member.camera_location_cluster_id = clc.id
                  AND direction_member.cluster_version = $4
                  AND cdb.binding_version = $5
                  AND cdb.is_selected
                  AND cdb.binding_status IN ('automatic', 'human_confirmed')
                ORDER BY
                    CASE WHEN cdb.binding_status = 'human_confirmed' THEN 0 ELSE 1 END,
                    cdb.binding_confidence DESC,
                    cdb.anchor_distance_m,
                    cdb.id
                LIMIT 1
            ) direction ON TRUE
            LEFT JOIN LATERAL (
                SELECT COUNT(DISTINCT cdb.direction_line_observation_id) AS direction_count
                FROM camera_direction_bindings cdb
                WHERE cdb.camera_observation_id = csr.camera_observation_id
                  AND cdb.binding_version = $5
                  AND cdb.is_selected
                  AND cdb.binding_status IN ('automatic', 'human_confirmed')
            ) direction_counts ON TRUE
        ),
        nearby AS (
            SELECT
                clc.id AS cluster_id,
                clc.representative_camera_observation_id,
                camera.placemark_name AS camera_name,
                camera.placemark_name AS evidence_camera_name,
                rp.description AS evidence_camera_description,
                ST_X(clc.representative_geom) AS lng,
                ST_Y(clc.representative_geom) AS lat,
                clc.member_count,
                'nearby_historical'::varchar AS selection_tier,
                GREATEST(
                    0.25,
                    0.55 - ST_Distance(
                        clc.representative_geom::geography,
                        canonical.representative_geom::geography
                    ) / 200.0
                ) AS candidate_confidence,
                NULL::varchar AS relationship_status,
                clc.cluster_status,
                NULL::uuid AS relationship_id,
                NULL::uuid AS layout_site_observation_id,
                NULL::uuid AS layout_raw_upload_id,
                NULL::text AS layout_source_file,
                NULL::timestamptz AS layout_uploaded_at,
                ST_Distance(
                    clc.representative_geom::geography,
                    canonical.representative_geom::geography
                ) AS distance_to_site_m,
                direction.suggested_centre_bearing AS azimuth,
                direction.suggested_coverage_angle,
                direction.source_positions::text AS direction_positions_json,
                COALESCE(direction_counts.direction_count, 0) AS evidence_direction_count,
                ru.created_at AS latest_timestamp
            FROM canonical
            JOIN camera_location_clusters clc
              ON clc.cluster_version = $4
             AND clc.cluster_status IN ('suggested', 'human_confirmed')
             AND ST_DWithin(
                 clc.representative_geom::geography,
                 canonical.representative_geom::geography,
                 $6
             )
            JOIN camera_observations camera
              ON camera.id = clc.representative_camera_observation_id
             AND (
                 canonical.task_family IN ('unknown', 'mixed')
                 OR camera.task_family IN ('unknown', 'mixed')
                 OR (
                     canonical.task_family IN ('od', 'ipod')
                     AND camera.task_family IN ('od', 'ipod')
                 )
                 OR canonical.task_family = camera.task_family
             )
            JOIN classification_decisions cd
              ON cd.id = camera.classification_decision_id
            JOIN raw_placemarks rp ON rp.id = cd.raw_placemark_id
            JOIN raw_uploads ru ON ru.id = rp.raw_upload_id
            LEFT JOIN LATERAL (
                SELECT
                    dlo.suggested_centre_bearing,
                    dlo.suggested_coverage_angle,
                    dlo.source_positions
                FROM camera_location_cluster_members direction_member
                JOIN camera_direction_bindings cdb
                  ON cdb.camera_observation_id = direction_member.camera_observation_id
                JOIN direction_line_observations dlo
                  ON dlo.id = cdb.direction_line_observation_id
                WHERE direction_member.camera_location_cluster_id = clc.id
                  AND direction_member.cluster_version = $4
                  AND cdb.binding_version = $5
                  AND cdb.is_selected
                  AND cdb.binding_status IN ('automatic', 'human_confirmed')
                ORDER BY
                    CASE WHEN cdb.binding_status = 'human_confirmed' THEN 0 ELSE 1 END,
                    cdb.binding_confidence DESC,
                    cdb.anchor_distance_m,
                    cdb.id
                LIMIT 1
            ) direction ON TRUE
            LEFT JOIN LATERAL (
                SELECT COUNT(DISTINCT cdb.direction_line_observation_id) AS direction_count
                FROM camera_direction_bindings cdb
                WHERE cdb.camera_observation_id = camera.id
                  AND cdb.binding_version = $5
                  AND cdb.is_selected
                  AND cdb.binding_status IN ('automatic', 'human_confirmed')
            ) direction_counts ON TRUE
        )
        SELECT * FROM related
        UNION ALL
        SELECT * FROM nearby
        ORDER BY selection_tier, candidate_confidence DESC, distance_to_site_m, cluster_id;
        """,
        canonical_site_id,
        settings.CANONICAL_SITE_VERSION,
        settings.CAMERA_SITE_RELATIONSHIP_VERSION,
        settings.CAMERA_LOCATION_CLUSTER_VERSION,
        settings.OBSERVATION_NORMALIZATION_VERSION,
        settings.NEARBY_HISTORICAL_CAMERA_RADIUS_M,
    )


async def fetch_nearby_canonical_site_rows(
    canonical_site_id: UUID,
    radius_m: float,
) -> list[asyncpg.Record]:
    """Return nearby task-compatible Canonical Sites for supplemental layouts."""
    pool = await get_pool()
    return await pool.fetch(
        """
        WITH target AS (
            SELECT id, representative_geom, task_family
            FROM canonical_sites
            WHERE id = $1
              AND canonical_version = $3
              AND canonical_status IN ('suggested', 'human_confirmed')
        )
        SELECT
            nearby.id AS canonical_site_id,
            representative.placemark_name AS canonical_name,
            ST_Distance(
                nearby.representative_geom::geography,
                target.representative_geom::geography
            ) AS distance_m
        FROM target
        JOIN canonical_sites nearby
          ON nearby.id <> target.id
         AND nearby.canonical_version = $3
         AND nearby.canonical_status IN ('suggested', 'human_confirmed')
         AND (
             target.task_family IN ('unknown', 'mixed')
             OR nearby.task_family IN ('unknown', 'mixed')
             OR (
                 target.task_family IN ('od', 'ipod')
                 AND nearby.task_family IN ('od', 'ipod')
             )
             OR target.task_family = nearby.task_family
         )
         AND ST_DWithin(
             nearby.representative_geom::geography,
             target.representative_geom::geography,
             $2
         )
        JOIN site_observations representative
          ON representative.id = nearby.representative_site_observation_id
        ORDER BY distance_m, nearby.id;
        """,
        canonical_site_id,
        radius_m,
        settings.CANONICAL_SITE_VERSION,
    )


async def fetch_nearby_canonical_camera_candidate_rows(
    canonical_site_id: UUID,
    nearby_canonical_site_id: UUID,
) -> list[asyncpg.Record]:
    """Return nearby Canonical Site relationship candidates measured to target."""
    pool = await get_pool()
    return await pool.fetch(
        """
        WITH target AS (
            SELECT id, representative_geom, task_family
            FROM canonical_sites
            WHERE id = $1
              AND canonical_version = $2
              AND canonical_status IN ('suggested', 'human_confirmed')
        ),
        nearby AS (
            SELECT id, representative_geom, task_family
            FROM canonical_sites
            WHERE id = $3
              AND canonical_version = $2
              AND canonical_status IN ('suggested', 'human_confirmed')
        )
        SELECT
            clc.id AS cluster_id,
            clc.representative_camera_observation_id,
            camera.placemark_name AS camera_name,
            evidence_camera.placemark_name AS evidence_camera_name,
            evidence_rp.description AS evidence_camera_description,
            ST_X(clc.representative_geom) AS lng,
            ST_Y(clc.representative_geom) AS lat,
            clc.member_count,
            'nearby_historical'::varchar AS selection_tier,
            GREATEST(
                0.25,
                LEAST(
                    0.58,
                    csr.relationship_confidence * 0.75
                    - ST_Distance(
                        clc.representative_geom::geography,
                        target.representative_geom::geography
                    ) / 500.0
                )
            ) AS candidate_confidence,
            csr.relationship_status,
            clc.cluster_status,
            NULL::uuid AS relationship_id,
            csm.site_observation_id AS layout_site_observation_id,
            site_rp.raw_upload_id AS layout_raw_upload_id,
            site_ru.source_file AS layout_source_file,
            site_ru.created_at AS layout_uploaded_at,
            ST_Distance(
                clc.representative_geom::geography,
                target.representative_geom::geography
            ) AS distance_to_site_m,
            direction.suggested_centre_bearing AS azimuth,
            direction.suggested_coverage_angle,
            direction.source_positions::text AS direction_positions_json,
            COALESCE(direction_counts.direction_count, 0) AS evidence_direction_count,
            ru.created_at AS latest_timestamp
        FROM target
        JOIN nearby
          ON (
              target.task_family IN ('unknown', 'mixed')
              OR nearby.task_family IN ('unknown', 'mixed')
              OR (
                  target.task_family IN ('od', 'ipod')
                  AND nearby.task_family IN ('od', 'ipod')
              )
              OR target.task_family = nearby.task_family
          )
        JOIN canonical_site_memberships csm
          ON csm.canonical_site_id = nearby.id
         AND csm.canonical_version = $2
         AND csm.membership_status IN ('suggested', 'human_confirmed')
        JOIN camera_site_relationships csr
          ON csr.site_observation_id = csm.site_observation_id
         AND csr.association_version = $4
         AND csr.relationship_status IN ('suggested', 'human_confirmed')
         AND csr.relationship_role = 'primary'
        JOIN site_observations layout_site
          ON layout_site.id = csm.site_observation_id
        JOIN raw_placemarks site_rp
          ON site_rp.id = layout_site.raw_placemark_id
        JOIN raw_uploads site_ru
          ON site_ru.id = site_rp.raw_upload_id
        JOIN camera_location_cluster_members clcm
          ON clcm.camera_observation_id = csr.camera_observation_id
         AND clcm.cluster_version = $5
        JOIN camera_location_clusters clc
          ON clc.id = clcm.camera_location_cluster_id
         AND clc.cluster_status IN ('suggested', 'human_confirmed')
        JOIN camera_observations camera
          ON camera.id = clc.representative_camera_observation_id
        JOIN camera_observations evidence_camera
          ON evidence_camera.id = csr.camera_observation_id
        JOIN raw_placemarks evidence_rp
          ON evidence_rp.id = evidence_camera.raw_placemark_id
        JOIN classification_decisions cd
          ON cd.id = camera.classification_decision_id
        JOIN raw_placemarks rp ON rp.id = cd.raw_placemark_id
        JOIN raw_uploads ru ON ru.id = rp.raw_upload_id
        LEFT JOIN LATERAL (
            SELECT
                dlo.suggested_centre_bearing,
                dlo.suggested_coverage_angle,
                dlo.source_positions
            FROM camera_location_cluster_members direction_member
            JOIN camera_direction_bindings cdb
              ON cdb.camera_observation_id = direction_member.camera_observation_id
            JOIN direction_line_observations dlo
              ON dlo.id = cdb.direction_line_observation_id
            WHERE direction_member.camera_location_cluster_id = clc.id
              AND direction_member.cluster_version = $5
              AND cdb.binding_version = $6
              AND cdb.is_selected
              AND cdb.binding_status IN ('automatic', 'human_confirmed')
            ORDER BY
                CASE WHEN cdb.binding_status = 'human_confirmed' THEN 0 ELSE 1 END,
                cdb.binding_confidence DESC,
                cdb.anchor_distance_m,
                cdb.id
            LIMIT 1
        ) direction ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT cdb.direction_line_observation_id) AS direction_count
            FROM camera_direction_bindings cdb
            WHERE cdb.camera_observation_id = csr.camera_observation_id
              AND cdb.binding_version = $6
              AND cdb.is_selected
              AND cdb.binding_status IN ('automatic', 'human_confirmed')
        ) direction_counts ON TRUE
        ORDER BY candidate_confidence DESC, distance_to_site_m, cluster_id;
        """,
        canonical_site_id,
        settings.CANONICAL_SITE_VERSION,
        nearby_canonical_site_id,
        settings.CAMERA_SITE_RELATIONSHIP_VERSION,
        settings.CAMERA_LOCATION_CLUSTER_VERSION,
        settings.OBSERVATION_NORMALIZATION_VERSION,
    )


async def insert_survey_points(
    points: list[SurveyPoint],
    source_filename: str,
    raw_upload_id: UUID | None = None,
) -> int:
    """Batch-insert parsed survey points into ``survey_history``.

    Parameters
    ----------
    points:
        List of parsed SurveyPoint models.
    source_filename:
        Original filename to record against every row.

    Returns
    -------
    int
        Number of rows inserted.
    """
    pool = await get_pool()

    insert_sql = """
        INSERT INTO survey_history (
            geom,
            azimuth,
            equipment_type,
            timestamp,
            layout_family,
            source_group,
            source_file,
            parser_version,
            import_batch_id,
            raw_upload_id,
            site_name,
            site_source_group,
            site_lat,
            site_lng,
            site_distance_m,
            required_camera_count,
            camera_count_source
        )
        VALUES (
            ST_SetSRID(ST_MakePoint($1, $2), 4326),
            $3,
            $4,
            COALESCE(
                (SELECT created_at FROM raw_uploads WHERE id = $10),
                NOW()
            ),
            $5,
            $6,
            $7,
            $8,
            $9,
            $10,
            $11,
            $12,
            $13,
            $14,
            $15,
            $16,
            $17
        );
    """

    params: list[tuple[object, ...]] = [
        (
            p.lng,       # $1 — longitude first for ST_MakePoint
            p.lat,       # $2 — latitude
            p.azimuth,   # $3
            p.equipment_type.value,  # $4
            p.layout_family,  # $5
            p.source_group,  # $6
            source_filename,  # $7
            settings.HISTORY_PARSER_VERSION,  # $8
            "",  # $9 populated after import batch creation
            raw_upload_id,  # $10
            p.site_name,  # $11
            p.site_source_group,  # $12
            p.site_lat,  # $13
            p.site_lng,  # $14
            p.site_distance_m,  # $15
            p.required_camera_count,  # $16
            p.camera_count_source,  # $17
        )
        for p in points
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM survey_history
                WHERE source_file = $1
                  AND parser_version = $2;
                """,
                source_filename,
                settings.HISTORY_PARSER_VERSION,
            )
            batch_id = await conn.fetchval(
                """
                INSERT INTO import_batches (
                    raw_upload_id,
                    source_file,
                    parser_version,
                    points_inserted
                )
                VALUES ($1, $2, $3, $4)
                RETURNING id;
                """,
                raw_upload_id,
                source_filename,
                settings.HISTORY_PARSER_VERSION,
                len(params),
            )
            batch_params = [
                (*param[:8], batch_id, *param[9:])
                for param in params
            ]
            await conn.executemany(insert_sql, batch_params)

    logger.info(
        "Inserted %d survey points from '%s'.",
        len(params),
        source_filename,
    )
    return len(params)
