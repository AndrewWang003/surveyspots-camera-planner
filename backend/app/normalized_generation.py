"""Read-only Canonical Site matching and normalized Camera generation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import json
from uuid import UUID

from .business_classifier import extract_required_camera_count
from .camera_generation_selector import (
    CameraGenerationCandidate,
    select_camera_locations,
)
from .camera_quantity import infer_camera_quantity
from .database import (
    fetch_canonical_camera_candidate_rows,
    fetch_canonical_count_decision_rows,
    fetch_nearby_canonical_camera_candidate_rows,
    fetch_nearby_canonical_site_rows,
    match_canonical_site_for_point,
    match_canonical_sites_for_polygon,
)
from .models import PinLocation, PolygonLocation
from .site_layout_selector import select_site_layout_preference

DEFAULT_REQUEST_CAMERA_COUNT = 2
NEARBY_CANONICAL_LAYOUT_RADIUS_M = 50.0


@dataclass(frozen=True)
class GeneratedCanonicalSite:
    canonical_site_id: str
    name: str
    lat: float
    lng: float
    request_sources: tuple[str, ...]
    match_distance_m: float
    required_camera_count: int | None
    camera_count_source: str
    selected_camera_count: int
    missing_camera_count: int | None
    generation_status: str
    description_note: str
    base_layout_frequency: int = 0
    historical_layout_count: int = 0
    icon_color: str = ""


@dataclass(frozen=True)
class CameraSiteCoverage:
    canonical_site_id: str
    site_name: str
    selection_tier: str
    selection_rank: int
    ranking_score: float = 0.0
    ranking_method: str = ""
    ranking_reasons: tuple[str, ...] = ()
    layout_role: str = "ungrouped_historical"
    layout_frequency: int = 0
    site_icon_color: str = ""


@dataclass
class GeneratedCameraLocation:
    camera_location_cluster_id: str
    representative_camera_observation_id: str
    name: str
    lat: float
    lng: float
    azimuth: float | None
    historical_observation_count: int
    latest_timestamp: datetime
    candidate_confidence: float
    direction_positions: tuple[tuple[float, ...], ...] = ()
    suggested_coverage_angle: float | None = None
    coverages: list[CameraSiteCoverage] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedGenerationOutput:
    sites: tuple[GeneratedCanonicalSite, ...]
    cameras: tuple[GeneratedCameraLocation, ...]
    unmatched_request_items: tuple[str, ...]


@dataclass
class _MatchedSite:
    canonical_site_id: UUID
    canonical_name: str
    lat: float
    lng: float
    match_distance_m: float
    request_sources: list[str]
    display_name: str
    explicit_count: int | None = None
    icon_color: str = ""


async def generate_from_request_geometries(
    pins: list[PinLocation],
    polygons: list[PolygonLocation],
    match_radius_m: float,
    maximum_sites: int,
) -> NormalizedGenerationOutput:
    """Match request geometry to Canonical Sites and select unique Cameras."""
    matched: dict[UUID, _MatchedSite] = {}
    unmatched: list[str] = []

    for index, pin in enumerate(pins, start=1):
        label = pin.name or f"Pin {index}"
        row = await match_canonical_site_for_point(
            pin.lat,
            pin.lng,
            match_radius_m,
            pin.task_family,
        )
        if row is None:
            unmatched.append(label)
            continue
        canonical_id = row["canonical_site_id"]
        item = matched.setdefault(
            canonical_id,
            _MatchedSite(
                canonical_site_id=canonical_id,
                canonical_name=row["canonical_name"] or label,
                lat=float(row["lat"]),
                lng=float(row["lng"]),
                match_distance_m=float(row["match_distance_m"]),
                request_sources=[],
                display_name=label,
            ),
        )
        item.request_sources.append(label)
        explicit_count = extract_required_camera_count(pin.description)
        if explicit_count is not None:
            item.explicit_count = explicit_count
        if pin.icon_color:
            item.icon_color = pin.icon_color

    for index, polygon in enumerate(polygons, start=1):
        label = polygon.name or f"Polygon {index}"
        rows = await match_canonical_sites_for_polygon(
            polygon.outer_ring,
            polygon.inner_rings,
        )
        if not rows:
            unmatched.append(label)
            continue
        for row in rows:
            canonical_id = row["canonical_site_id"]
            item = matched.setdefault(
                canonical_id,
                _MatchedSite(
                    canonical_site_id=canonical_id,
                    canonical_name=row["canonical_name"] or label,
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                    match_distance_m=float(row["match_distance_m"]),
                    request_sources=[],
                    display_name=row["canonical_name"] or label,
                ),
            )
            item.request_sources.append(label)

    if len(matched) > maximum_sites:
        raise ValueError(
            f"Request matched {len(matched)} Canonical Sites; limit is {maximum_sites}."
        )

    generated_sites: list[GeneratedCanonicalSite] = []
    generated_cameras: dict[str, GeneratedCameraLocation] = {}
    for matched_site in sorted(
        matched.values(),
        key=lambda item: (item.canonical_name, str(item.canonical_site_id)),
    ):
        count_rows = await fetch_canonical_count_decision_rows(
            matched_site.canonical_site_id
        )
        required_count, count_source = choose_request_camera_count(
            matched_site.explicit_count,
            count_rows,
        )
        candidate_rows = await fetch_canonical_camera_candidate_rows(
            matched_site.canonical_site_id
        )
        if count_source == "insufficient_history":
            nearby_count_rows, nearby_candidate_rows = await _nearby_canonical_evidence(
                matched_site.canonical_site_id
            )
            if nearby_count_rows:
                required_count, count_source = choose_request_camera_count(
                    None,
                    nearby_count_rows,
                )
                count_source = f"nearby_canonical_{count_source}"
            candidate_rows = [
                *candidate_rows,
                *nearby_candidate_rows,
            ]
            if required_count is None:
                required_count = DEFAULT_REQUEST_CAMERA_COUNT
                count_source = "default_request_count"
        layout_preference = select_site_layout_preference(candidate_rows)
        candidates = [
            CameraGenerationCandidate(
                camera_location_cluster_id=str(row["cluster_id"]),
                representative_camera_observation_id=str(
                    row["representative_camera_observation_id"]
                ),
                selection_tier=row["selection_tier"],
                distance_to_site_m=float(row["distance_to_site_m"]),
                candidate_confidence=float(row["candidate_confidence"]),
                source_relationship_id=(
                    str(row["relationship_id"])
                    if row["relationship_id"] is not None
                    else None
                ),
                historical_observation_count=int(row["member_count"]),
                relationship_status=row["relationship_status"] or "suggested",
                cluster_status=row["cluster_status"] or "suggested",
                layout_role=_candidate_layout_role(
                    row,
                    layout_preference.role_for_cluster(
                        str(row["cluster_id"]),
                        row["selection_tier"],
                    ),
                ),
                layout_frequency=layout_preference.cluster_frequencies.get(
                    str(row["cluster_id"]),
                    0,
                ),
                represented_camera_count=infer_camera_quantity(
                    row["evidence_camera_name"] or row["camera_name"] or "",
                    row["evidence_camera_description"] or "",
                    int(row["evidence_direction_count"] or 0),
                ),
            )
            for row in candidate_rows
        ]
        result = select_camera_locations(
            str(matched_site.canonical_site_id),
            required_count,
            candidates,
        )
        display_name = matched_site.display_name
        generated_sites.append(
            GeneratedCanonicalSite(
                canonical_site_id=str(matched_site.canonical_site_id),
                name=display_name,
                lat=matched_site.lat,
                lng=matched_site.lng,
                request_sources=tuple(dict.fromkeys(matched_site.request_sources)),
                match_distance_m=matched_site.match_distance_m,
                required_camera_count=required_count,
                camera_count_source=count_source,
                selected_camera_count=result.selected_camera_count,
                missing_camera_count=result.missing_reliable_location_count,
                generation_status=result.generation_status,
                description_note=result.site_description_note,
                base_layout_frequency=layout_preference.base_layout_frequency,
                historical_layout_count=layout_preference.distinct_layout_count,
                icon_color=matched_site.icon_color,
            )
        )
        for selection in result.selected_locations:
            row = _find_selected_candidate_row(candidate_rows, selection)
            cluster_id = selection.camera_location_cluster_id
            camera = generated_cameras.get(cluster_id)
            if camera is None:
                camera = GeneratedCameraLocation(
                    camera_location_cluster_id=cluster_id,
                    representative_camera_observation_id=(
                        selection.representative_camera_observation_id
                    ),
                    name=row["camera_name"] or "Camera Location",
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                    azimuth=(
                        float(row["azimuth"])
                        if row["azimuth"] is not None
                        else None
                    ),
                    historical_observation_count=int(row["member_count"]),
                    latest_timestamp=row["latest_timestamp"],
                    candidate_confidence=selection.candidate_confidence,
                    direction_positions=_parse_direction_positions(
                        row["direction_positions_json"]
                    ),
                    suggested_coverage_angle=(
                        float(row["suggested_coverage_angle"])
                        if row["suggested_coverage_angle"] is not None
                        else None
                    ),
                )
                generated_cameras[cluster_id] = camera
            camera.coverages.append(
                CameraSiteCoverage(
                    canonical_site_id=str(matched_site.canonical_site_id),
                    site_name=display_name,
                    selection_tier=selection.selection_tier,
                    selection_rank=selection.selection_rank,
                    ranking_score=selection.ranking_score,
                    ranking_method=selection.ranking_method,
                    ranking_reasons=selection.ranking_reasons,
                    layout_role=selection.layout_role,
                    layout_frequency=selection.layout_frequency,
                    site_icon_color=matched_site.icon_color,
                )
            )

    return NormalizedGenerationOutput(
        sites=tuple(generated_sites),
        cameras=tuple(
            sorted(
                generated_cameras.values(),
                key=_generated_camera_sort_key,
            )
        ),
        unmatched_request_items=tuple(unmatched),
    )


def choose_request_camera_count(
    explicit_count: int | None,
    decision_rows,
) -> tuple[int | None, str]:
    """Choose request count: request Description, history mode, then explicit history."""
    if explicit_count is not None:
        return explicit_count, "request_explicit_description"
    historical_mode_rows = [
        row for row in decision_rows if row["camera_count_source"] == "historical_mode"
    ]
    if historical_mode_rows:
        return _mode_from_decision_rows(historical_mode_rows), "historical_mode"
    explicit_history_rows = [
        row
        for row in decision_rows
        if row["camera_count_source"] == "explicit_description"
    ]
    if explicit_history_rows:
        return _mode_from_decision_rows(
            explicit_history_rows
        ), "historical_explicit_mode"
    return None, "insufficient_history"


async def _nearby_canonical_evidence(canonical_site_id: UUID) -> tuple[list, list]:
    count_rows = []
    candidate_rows = []
    for nearby_site in await fetch_nearby_canonical_site_rows(
        canonical_site_id,
        NEARBY_CANONICAL_LAYOUT_RADIUS_M,
    ):
        nearby_id = nearby_site["canonical_site_id"]
        nearby_counts = await fetch_canonical_count_decision_rows(nearby_id)
        if nearby_counts:
            count_rows.extend(nearby_counts)
        nearby_candidates = await fetch_nearby_canonical_camera_candidate_rows(
            canonical_site_id,
            nearby_id,
        )
        candidate_rows.extend(nearby_candidates)
    return count_rows, candidate_rows


def _candidate_layout_role(row, fallback: str) -> str:
    if row["selection_tier"] == "nearby_historical" and row["relationship_id"] is None:
        if row["layout_site_observation_id"] is not None:
            return "supplemental_historical"
    return fallback


def _generated_camera_sort_key(camera: GeneratedCameraLocation) -> tuple:
    if not camera.coverages:
        return ("", 0, camera.camera_location_cluster_id)
    coverage = min(
        camera.coverages,
        key=lambda item: (
            item.site_name,
            item.selection_rank,
            item.canonical_site_id,
        ),
    )
    return (
        coverage.site_name,
        coverage.selection_rank,
        camera.camera_location_cluster_id,
    )


def _mode_from_decision_rows(rows) -> int:
    votes = Counter(int(row["required_camera_count"]) for row in rows)
    return min(votes, key=lambda count: (-votes[count], -count))


def _find_selected_candidate_row(rows, selection):
    matches = [
        row
        for row in rows
        if str(row["cluster_id"]) == selection.camera_location_cluster_id
        and row["selection_tier"] == selection.selection_tier
    ]
    if selection.source_relationship_id is not None:
        exact_relationship_matches = [
            row
            for row in matches
            if row["relationship_id"] is not None
            and str(row["relationship_id"]) == selection.source_relationship_id
        ]
        if exact_relationship_matches:
            return exact_relationship_matches[0]
    else:
        nearby_matches = [row for row in matches if row["relationship_id"] is None]
        if nearby_matches:
            return min(
                nearby_matches,
                key=lambda row: (
                    float(row["distance_to_site_m"]),
                    str(row["representative_camera_observation_id"]),
                ),
            )
    return min(
        matches,
        key=lambda row: (
            -float(row["candidate_confidence"]),
            float(row["distance_to_site_m"]),
            str(row["representative_camera_observation_id"]),
        ),
    )


def _parse_direction_positions(value: str | None) -> tuple[tuple[float, ...], ...]:
    """Parse preserved KML positions from a selected historical Direction Line."""
    if not value:
        return ()
    try:
        positions = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(positions, list):
        return ()

    parsed: list[tuple[float, ...]] = []
    for position in positions:
        if (
            not isinstance(position, list)
            or len(position) < 2
            or any(
                not isinstance(coordinate, (int, float)) or isinstance(coordinate, bool)
                for coordinate in position
            )
        ):
            return ()
        parsed.append(tuple(float(coordinate) for coordinate in position))
    return tuple(parsed) if len(parsed) >= 2 else ()
