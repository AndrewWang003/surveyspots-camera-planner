"""Build versioned Camera, Direction Line, and binding observations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .business_classifier import ClassificationDecision
from .direction_geometry import (
    Position,
    ThreePointDirectionAnalysis,
    analyse_three_point_direction,
    extract_linestring_positions,
    extract_point_position,
    haversine_metres,
)
from .raw_placemark_parser import RawPlacemarkRecord


AUTO_BIND_RADIUS_M = 2.0
REVIEW_BIND_RADIUS_M = 4.0
AMBIGUOUS_DISTANCE_DELTA_M = 0.5


@dataclass(frozen=True)
class CameraObservationCandidate:
    placemark_index: int
    name: str
    task_family: str
    classification_confidence: float
    position: Position
    location_method: str


@dataclass
class DirectionObservationCandidate:
    placemark_index: int
    name: str
    task_family: str
    classification_confidence: float
    positions: tuple[Position, ...]
    analysis: ThreePointDirectionAnalysis | None
    geometry_status: str
    binding_state: str
    requires_review: bool


@dataclass(frozen=True)
class CameraDirectionBindingCandidate:
    camera_placemark_index: int
    direction_placemark_index: int
    anchor_distance_m: float
    binding_status: str
    is_selected: bool
    binding_confidence: float
    binding_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ObservationBundle:
    cameras: tuple[CameraObservationCandidate, ...]
    directions: tuple[DirectionObservationCandidate, ...]
    bindings: tuple[CameraDirectionBindingCandidate, ...]


def build_observation_bundle(
    placemarks: list[RawPlacemarkRecord],
    decisions: list[ClassificationDecision],
) -> ObservationBundle:
    """Build observations and same-Folder Camera/Direction binding candidates."""
    decision_by_index = {decision.placemark_index: decision for decision in decisions}
    placemark_by_index = {
        placemark.placemark_index: placemark for placemark in placemarks
    }
    cameras: list[CameraObservationCandidate] = []
    directions: list[DirectionObservationCandidate] = []

    for placemark in placemarks:
        decision = decision_by_index.get(placemark.placemark_index)
        if decision is None:
            continue
        if decision.business_role == "camera_location":
            position = extract_point_position(placemark)
            if position is not None:
                cameras.append(
                    CameraObservationCandidate(
                        placemark_index=placemark.placemark_index,
                        name=placemark.placemark_name,
                        task_family=decision.task_family,
                        classification_confidence=decision.classification_confidence,
                        position=position,
                        location_method="source_point",
                    )
                )
        elif decision.business_role == "camera_direction":
            positions = extract_linestring_positions(placemark)
            analysis = analyse_three_point_direction(positions)
            standard_geometry = analysis is not None
            directions.append(
                DirectionObservationCandidate(
                    placemark_index=placemark.placemark_index,
                    name=placemark.placemark_name,
                    task_family=decision.task_family,
                    classification_confidence=decision.classification_confidence,
                    positions=positions,
                    analysis=analysis,
                    geometry_status=(
                        "standard_three_point"
                        if standard_geometry
                        else "nonstandard_review"
                    ),
                    binding_state=("unbound" if standard_geometry else "nonstandard_review"),
                    requires_review=not standard_geometry,
                )
            )

    cameras_by_folder: dict[
        tuple[str, ...], list[CameraObservationCandidate]
    ] = defaultdict(list)
    for camera in cameras:
        folder_path = placemark_by_index[camera.placemark_index].folder_path
        cameras_by_folder[folder_path].append(camera)

    bindings: list[CameraDirectionBindingCandidate] = []
    for direction in directions:
        if direction.analysis is None:
            continue
        folder_path = placemark_by_index[direction.placemark_index].folder_path
        ranked = sorted(
            (
                (
                    haversine_metres(
                        direction.analysis.camera_anchor,
                        camera.position,
                    ),
                    camera,
                )
                for camera in cameras_by_folder.get(folder_path, [])
            ),
            key=lambda item: (item[0], item[1].placemark_index),
        )
        nearby = [item for item in ranked if item[0] <= REVIEW_BIND_RADIUS_M]
        if not nearby:
            synthetic_camera = CameraObservationCandidate(
                placemark_index=direction.placemark_index,
                name=f"Camera Location - {direction.name or 'Direction'}",
                task_family=direction.task_family,
                classification_confidence=min(
                    direction.classification_confidence,
                    0.88,
                ),
                position=direction.analysis.camera_anchor,
                location_method="direction_point_2",
            )
            cameras.append(synthetic_camera)
            direction.binding_state = "automatic"
            direction.requires_review = False
            bindings.append(
                CameraDirectionBindingCandidate(
                    camera_placemark_index=synthetic_camera.placemark_index,
                    direction_placemark_index=direction.placemark_index,
                    anchor_distance_m=0.0,
                    binding_status="automatic",
                    is_selected=True,
                    binding_confidence=0.88,
                    binding_reasons=(
                        "No Camera Point exists within 4 metres; Point 2 is used as the derived Camera Location.",
                    ),
                )
            )
            continue

        ambiguous = (
            len(nearby) > 1
            and nearby[1][0] - nearby[0][0] < AMBIGUOUS_DISTANCE_DELTA_M
        )
        nearest_distance, nearest_camera = nearby[0]
        if nearest_distance <= AUTO_BIND_RADIUS_M and not ambiguous:
            direction.binding_state = "automatic"
            direction.requires_review = False
            bindings.append(
                CameraDirectionBindingCandidate(
                    camera_placemark_index=nearest_camera.placemark_index,
                    direction_placemark_index=direction.placemark_index,
                    anchor_distance_m=nearest_distance,
                    binding_status="automatic",
                    is_selected=True,
                    binding_confidence=max(0.80, 1.0 - nearest_distance / 10.0),
                    binding_reasons=(
                        "Point 2 is within 2 metres of the nearest Camera Point in the same source Folder.",
                    ),
                )
            )
            continue

        direction.binding_state = "review_candidate"
        direction.requires_review = True
        review_candidates = nearby if ambiguous else nearby[:1]
        for distance, camera in review_candidates:
            bindings.append(
                CameraDirectionBindingCandidate(
                    camera_placemark_index=camera.placemark_index,
                    direction_placemark_index=direction.placemark_index,
                    anchor_distance_m=distance,
                    binding_status="review_candidate",
                    is_selected=False,
                    binding_confidence=max(0.35, 0.70 - distance / 20.0),
                    binding_reasons=(
                        "Camera anchor is within the 4-metre review radius but is not eligible for automatic binding.",
                    ),
                )
            )

    return ObservationBundle(
        cameras=tuple(cameras),
        directions=tuple(directions),
        bindings=tuple(bindings),
    )
