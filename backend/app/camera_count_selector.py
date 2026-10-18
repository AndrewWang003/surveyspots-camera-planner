"""Select required Camera counts from explicit requirements or history."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .direction_geometry import Position, haversine_metres
from .task_family import task_families_compatible


@dataclass(frozen=True)
class SiteCameraCountEvidence:
    site_observation_id: str
    raw_upload_id: str
    position: Position
    task_family: str
    required_camera_count: int | None
    camera_count_source: str
    primary_camera_location_ids: tuple[str, ...]
    observed_order: float
    canonical_site_id: str | None = None
    primary_camera_count: int | None = None


@dataclass(frozen=True)
class SiteCameraCountDecision:
    site_observation_id: str
    required_camera_count: int | None
    camera_count_source: str
    decision_method: str
    decision_confidence: float
    decision_reasons: tuple[str, ...]
    supporting_site_observation_ids: tuple[str, ...]
    historical_count_votes: tuple[tuple[int, int], ...]
    requires_review: bool


def select_site_camera_count(
    target: SiteCameraCountEvidence,
    history: list[SiteCameraCountEvidence],
    site_equivalence_radius_m: float,
) -> SiteCameraCountDecision:
    """Apply explicit-count priority, then the mode of equivalent layouts."""
    if target.required_camera_count is not None:
        primary_count = _historical_primary_count(target)
        if primary_count > target.required_camera_count:
            return SiteCameraCountDecision(
                site_observation_id=target.site_observation_id,
                required_camera_count=primary_count,
                camera_count_source="explicit_description_conflict_primary",
                decision_method="explicit_description_primary_conflict",
                decision_confidence=0.92,
                decision_reasons=(
                    "The Site description explicitly specifies the required Camera count.",
                    "Primary Camera evidence names more Cameras than the explicit description.",
                    "The larger primary Camera evidence count is used automatically because named primary Camera evidence is stronger than stale description text.",
                ),
                supporting_site_observation_ids=(target.site_observation_id,),
                historical_count_votes=((target.required_camera_count, 1), (primary_count, 1)),
                requires_review=False,
            )
        return SiteCameraCountDecision(
            site_observation_id=target.site_observation_id,
            required_camera_count=target.required_camera_count,
            camera_count_source="explicit_description",
            decision_method="explicit_description",
            decision_confidence=0.99,
            decision_reasons=(
                "The Site description explicitly specifies the required Camera count.",
                "Explicit Site requirements take precedence over historical layout counts.",
            ),
            supporting_site_observation_ids=(target.site_observation_id,),
            historical_count_votes=(),
            requires_review=False,
        )

    eligible = [
        item
        for item in history
        if _is_equivalent_layout(target, item, site_equivalence_radius_m)
        and _historical_primary_count(item) > 0
    ]
    if not eligible:
        return SiteCameraCountDecision(
            site_observation_id=target.site_observation_id,
            required_camera_count=None,
            camera_count_source="insufficient_history",
            decision_method="historical_mode",
            decision_confidence=0.0,
            decision_reasons=(
                "The Site has no explicit Camera count and no equivalent historical layout with primary Camera locations.",
            ),
            supporting_site_observation_ids=(),
            historical_count_votes=(),
            requires_review=True,
        )

    samples = [
        (
            item,
            _historical_primary_count(item),
        )
        for item in eligible
    ]
    votes = Counter(count for _, count in samples)
    selected_count = min(
        votes,
        key=lambda count: (-votes[count], -count),
    )
    supporting = tuple(
        item.site_observation_id
        for item, count in sorted(
            samples,
            key=lambda sample: (
                -sample[0].observed_order,
                sample[0].site_observation_id,
            ),
        )
        if count == selected_count
    )
    winning_votes = votes[selected_count]
    confidence = min(0.95, 0.60 + 0.35 * winning_votes / len(samples))
    return SiteCameraCountDecision(
        site_observation_id=target.site_observation_id,
        required_camera_count=selected_count,
        camera_count_source="historical_mode",
        decision_method="historical_mode",
        decision_confidence=confidence,
        decision_reasons=(
            "The Site description does not specify a Camera count.",
            "The selected count is the mode of primary Camera Location Cluster counts from equivalent historical Site layouts.",
            "Ties are resolved by the larger Camera count so optional historical locations are surfaced for review.",
        ),
        supporting_site_observation_ids=supporting,
        historical_count_votes=tuple(sorted(votes.items())),
        requires_review=False,
    )


def _is_equivalent_layout(
    target: SiteCameraCountEvidence,
    candidate: SiteCameraCountEvidence,
    radius_m: float,
) -> bool:
    if target.site_observation_id == candidate.site_observation_id:
        return True
    if target.canonical_site_id is not None and candidate.canonical_site_id is not None:
        return target.canonical_site_id == candidate.canonical_site_id
    if target.raw_upload_id == candidate.raw_upload_id:
        return False
    if not task_families_compatible(target.task_family, candidate.task_family):
        return False
    return haversine_metres(target.position, candidate.position) <= radius_m


def _historical_primary_count(item: SiteCameraCountEvidence) -> int:
    if item.primary_camera_count is not None:
        return item.primary_camera_count
    return len(set(item.primary_camera_location_ids))
