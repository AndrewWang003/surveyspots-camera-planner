"""Orchestration for the versioned raw and classification ingestion layers."""

from __future__ import annotations

from uuid import UUID

from .business_classifier import classify_raw_placemarks
from .camera_site_relationships import build_camera_site_relationship_bundle
from .database import (
    fetch_selected_raw_upload_layers,
    reconcile_suggested_classification_scope,
    rebuild_camera_location_clusters,
    rebuild_canonical_sites,
    rebuild_site_camera_count_decisions,
    rebuild_site_camera_generation_results,
    store_camera_site_relationship_bundle,
    store_classification_decisions,
    store_observation_bundle,
    store_polygon_scope_bundle,
    store_raw_placemarks,
)
from .historical_layer_review import (
    apply_layer_review_overrides,
    filter_selected_placemarks,
    validate_selected_layers,
)
from .observation_builder import build_observation_bundle
from .raw_placemark_parser import extract_raw_placemarks
from .polygon_scope import build_polygon_scope_bundle


async def persist_normalized_ingestion_layers(
    raw_upload_id: UUID,
    kml_text: str,
    source_filename: str,
    selected_layers: dict[str, str] | None = None,
) -> tuple[int, ...]:
    """Persist raw evidence and only the human-reviewed normalized Layer scope."""
    placemarks = extract_raw_placemarks(kml_text)
    raw_count = await store_raw_placemarks(raw_upload_id, placemarks)
    if selected_layers is None:
        selected_layers = await fetch_selected_raw_upload_layers(raw_upload_id)
    if selected_layers is not None:
        validate_selected_layers(selected_layers)
    selected_placemarks = filter_selected_placemarks(placemarks, selected_layers)
    await reconcile_suggested_classification_scope(
        raw_upload_id,
        [placemark.placemark_index for placemark in selected_placemarks],
    )
    decisions = classify_raw_placemarks(selected_placemarks, source_filename)
    decisions = apply_layer_review_overrides(
        selected_placemarks,
        decisions,
        selected_layers,
    )
    decision_count = await store_classification_decisions(raw_upload_id, decisions)
    observations = build_observation_bundle(selected_placemarks, decisions)
    camera_count, direction_count, binding_count = await store_observation_bundle(
        raw_upload_id,
        observations,
    )
    camera_site_bundle = build_camera_site_relationship_bundle(
        selected_placemarks,
        decisions,
        observations.cameras,
    )
    site_count, relationship_count, relationship_review_count = (
        await store_camera_site_relationship_bundle(
            raw_upload_id,
            camera_site_bundle,
        )
    )
    polygon_scope_bundle = build_polygon_scope_bundle(selected_placemarks, decisions)
    polygon_count, polygon_membership_count = await store_polygon_scope_bundle(
        raw_upload_id,
        polygon_scope_bundle,
    )
    canonical_site_count, canonical_member_count, canonical_scope_count = (
        await rebuild_canonical_sites()
    )
    cluster_count, cluster_member_count = await rebuild_camera_location_clusters()
    camera_count_decision_count = await rebuild_site_camera_count_decisions()
    generation_result_count, generation_selection_count = (
        await rebuild_site_camera_generation_results()
    )
    return (
        raw_count,
        decision_count,
        camera_count,
        direction_count,
        binding_count,
        site_count,
        relationship_count,
        relationship_review_count,
        polygon_count,
        polygon_membership_count,
        canonical_site_count,
        canonical_member_count,
        canonical_scope_count,
        cluster_count,
        cluster_member_count,
        camera_count_decision_count,
        generation_result_count,
        generation_selection_count,
    )
