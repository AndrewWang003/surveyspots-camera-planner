"""Select de-duplicated Camera locations in strict evidence tiers."""

from __future__ import annotations

from dataclasses import dataclass


TIER_PRIORITY = {
    "primary": 0,
    "fallback": 1,
    "nearby_historical": 2,
}
RANKING_METHOD = "weighted_evidence_v1"
CONFIDENCE_WEIGHT = 0.70
PROXIMITY_WEIGHT = 0.25
HISTORICAL_SUPPORT_WEIGHT = 0.05
MAX_PROXIMITY_DISTANCE_M = 250.0
MAX_HISTORICAL_SUPPORT_COUNT = 5
LAYOUT_ROLE_PRIORITY = {
    "base_layout": 0,
    "supplemental_historical": 1,
    "ungrouped_historical": 2,
}


@dataclass(frozen=True)
class CameraGenerationCandidate:
    camera_location_cluster_id: str
    representative_camera_observation_id: str
    selection_tier: str
    distance_to_site_m: float
    candidate_confidence: float
    source_relationship_id: str | None = None
    historical_observation_count: int = 1
    relationship_status: str = "suggested"
    cluster_status: str = "suggested"
    layout_role: str = "ungrouped_historical"
    layout_frequency: int = 0
    represented_camera_count: int = 1


@dataclass(frozen=True)
class SelectedCameraLocation:
    camera_location_cluster_id: str
    representative_camera_observation_id: str
    selection_tier: str
    selection_rank: int
    distance_to_site_m: float
    candidate_confidence: float
    source_relationship_id: str | None
    ranking_score: float
    ranking_method: str
    ranking_reasons: tuple[str, ...]
    layout_role: str
    layout_frequency: int
    represented_camera_count: int


@dataclass(frozen=True)
class SiteCameraGenerationResult:
    site_observation_id: str
    required_camera_count: int | None
    selected_camera_count: int
    selected_locations: tuple[SelectedCameraLocation, ...]
    missing_reliable_location_count: int | None
    generation_status: str
    site_description_note: str
    generation_reasons: tuple[str, ...]
    requires_review: bool


def select_camera_locations(
    site_observation_id: str,
    required_camera_count: int | None,
    candidates: list[CameraGenerationCandidate],
) -> SiteCameraGenerationResult:
    """Select primary, then fallback, then nearby historical Clusters."""
    if required_camera_count is None:
        return SiteCameraGenerationResult(
            site_observation_id=site_observation_id,
            required_camera_count=None,
            selected_camera_count=0,
            selected_locations=(),
            missing_reliable_location_count=None,
            generation_status="requires_review",
            site_description_note=(
                "Camera count requires review before reliable locations can be generated."
            ),
            generation_reasons=(
                "No approved required Camera count is available for this Site.",
            ),
            requires_review=True,
        )
    if required_camera_count <= 0:
        raise ValueError("required_camera_count must be greater than zero")

    best_by_cluster: dict[str, CameraGenerationCandidate] = {}
    for candidate in candidates:
        if candidate.selection_tier not in TIER_PRIORITY:
            raise ValueError(f"Unsupported selection tier: {candidate.selection_tier}")
        if candidate.layout_role not in LAYOUT_ROLE_PRIORITY:
            raise ValueError(f"Unsupported layout role: {candidate.layout_role}")
        if candidate.represented_camera_count <= 0:
            raise ValueError("represented_camera_count must be greater than zero")
        existing = best_by_cluster.get(candidate.camera_location_cluster_id)
        if existing is None or _candidate_sort_key(candidate) < _candidate_sort_key(
            existing
        ):
            best_by_cluster[candidate.camera_location_cluster_id] = candidate

    ranked = sorted(best_by_cluster.values(), key=_candidate_sort_key)
    chosen = []
    represented_total = 0
    for candidate in ranked:
        if represented_total >= required_camera_count:
            break
        chosen.append(candidate)
        represented_total += candidate.represented_camera_count
    selected = tuple(
        SelectedCameraLocation(
            camera_location_cluster_id=candidate.camera_location_cluster_id,
            representative_camera_observation_id=(
                candidate.representative_camera_observation_id
            ),
            selection_tier=candidate.selection_tier,
            selection_rank=index,
            distance_to_site_m=candidate.distance_to_site_m,
            candidate_confidence=candidate.candidate_confidence,
            source_relationship_id=candidate.source_relationship_id,
            ranking_score=_candidate_ranking_score(candidate),
            ranking_method=RANKING_METHOD,
            ranking_reasons=_candidate_ranking_reasons(candidate),
            layout_role=candidate.layout_role,
            layout_frequency=candidate.layout_frequency,
            represented_camera_count=candidate.represented_camera_count,
        )
        for index, candidate in enumerate(chosen, start=1)
    )
    selected_camera_count = sum(
        selection.represented_camera_count for selection in selected
    )
    missing = max(0, required_camera_count - selected_camera_count)
    if missing:
        note = (
            f"Reliable camera coverage available: {selected_camera_count} of "
            f"{required_camera_count} {_camera_word(required_camera_count)} "
            f"across {len(selected)} {_location_word(len(selected))}. "
            f"Missing {missing} reliable {_camera_word(missing)}."
        )
        status = "partial"
    else:
        note = (
            f"Required {_camera_word(required_camera_count)}: {required_camera_count}. "
            f"Reliable camera coverage selected: {selected_camera_count} "
            f"across {len(selected)} {_location_word(len(selected))}."
        )
        status = "complete"

    tier_counts = {
        tier: sum(item.selection_tier == tier for item in selected)
        for tier in TIER_PRIORITY
    }
    return SiteCameraGenerationResult(
        site_observation_id=site_observation_id,
        required_camera_count=required_camera_count,
        selected_camera_count=selected_camera_count,
        selected_locations=selected,
        missing_reliable_location_count=missing,
        generation_status=status,
        site_description_note=note,
        generation_reasons=(
            "Camera Location Clusters are de-duplicated before selection.",
            (
                "Selection used strict tier order: primary, fallback, then nearby historical."
            ),
            (
                "Within each tier, human-confirmed evidence precedes the "
                "weighted evidence score."
            ),
            (
                "Weighted evidence uses relationship confidence 70%, proximity "
                "25%, and historical observation support 5%."
            ),
            (
                "Selected tier counts: "
                f"primary={tier_counts['primary']}, "
                f"fallback={tier_counts['fallback']}, "
                f"nearby_historical={tier_counts['nearby_historical']}."
            ),
        ),
        requires_review=missing > 0,
    )


def _candidate_sort_key(candidate: CameraGenerationCandidate) -> tuple:
    return (
        TIER_PRIORITY[candidate.selection_tier],
        0 if _is_human_confirmed(candidate) else 1,
        LAYOUT_ROLE_PRIORITY[candidate.layout_role],
        -candidate.layout_frequency,
        -_candidate_ranking_score(candidate),
        candidate.distance_to_site_m,
        candidate.camera_location_cluster_id,
        candidate.representative_camera_observation_id,
    )


def _candidate_ranking_score(candidate: CameraGenerationCandidate) -> float:
    """Return the explainable score used only within a strict evidence tier."""
    confidence_score = min(1.0, max(0.0, candidate.candidate_confidence))
    proximity_score = max(
        0.0,
        1.0 - candidate.distance_to_site_m / MAX_PROXIMITY_DISTANCE_M,
    )
    support_score = min(
        1.0,
        max(1, candidate.historical_observation_count)
        / MAX_HISTORICAL_SUPPORT_COUNT,
    )
    return round(
        confidence_score * CONFIDENCE_WEIGHT
        + proximity_score * PROXIMITY_WEIGHT
        + support_score * HISTORICAL_SUPPORT_WEIGHT,
        6,
    )


def _candidate_ranking_reasons(
    candidate: CameraGenerationCandidate,
) -> tuple[str, ...]:
    confirmation_reason = (
        "Human-confirmed relationship or Cluster evidence takes precedence."
        if _is_human_confirmed(candidate)
        else "The candidate is based on reviewable rule-generated evidence."
    )
    return (
        f"Evidence tier: {candidate.selection_tier}.",
        (
            f"Historical layout role: {candidate.layout_role}; "
            f"project frequency={candidate.layout_frequency}."
        ),
        confirmation_reason,
        (
            "Weighted score uses relationship confidence 70%, proximity 25%, "
            "and historical observation support 5%."
        ),
        (
            f"Inputs: confidence={candidate.candidate_confidence:.3f}, "
            f"distance={candidate.distance_to_site_m:.1f} m, "
            f"historical_observations={candidate.historical_observation_count}, "
            f"represented_cameras={candidate.represented_camera_count}."
        ),
    )


def _is_human_confirmed(candidate: CameraGenerationCandidate) -> bool:
    return (
        candidate.relationship_status == "human_confirmed"
        or candidate.cluster_status == "human_confirmed"
    )


def _camera_word(count: int) -> str:
    return "camera" if count == 1 else "cameras"


def _location_word(count: int) -> str:
    return "location" if count == 1 else "locations"
