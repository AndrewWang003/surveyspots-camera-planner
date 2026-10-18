"""Build deterministic, non-chain Canonical Sites from Site observations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .direction_geometry import Position, haversine_metres
from .task_family import task_families_compatible


@dataclass(frozen=True)
class CanonicalSiteEvidence:
    site_observation_id: str
    raw_upload_id: str
    position: Position
    task_family: str


@dataclass(frozen=True)
class CanonicalSiteCandidate:
    canonical_key: str
    members: tuple[CanonicalSiteEvidence, ...]
    representative_site_observation_id: str
    representative_position: Position
    task_family: str
    diameter_m: float
    match_confidence: float
    match_reasons: tuple[str, ...]


def build_canonical_sites(
    evidence: list[CanonicalSiteEvidence],
    maximum_diameter_m: float,
    canonical_version: str,
) -> tuple[CanonicalSiteCandidate, ...]:
    """Use complete-link spatial matching without same-upload Site merging."""
    if maximum_diameter_m <= 0:
        raise ValueError("maximum_diameter_m must be greater than zero")
    ordered = sorted(evidence, key=lambda item: item.site_observation_id)
    if len({item.site_observation_id for item in ordered}) != len(ordered):
        raise ValueError("Site observation IDs must be unique")

    distances = {
        (first.site_observation_id, second.site_observation_id): haversine_metres(
            first.position,
            second.position,
        )
        for index, first in enumerate(ordered)
        for second in ordered[index + 1 :]
    }
    working: list[tuple[CanonicalSiteEvidence, ...]] = [(item,) for item in ordered]
    while True:
        candidates = []
        for first_index, first_cluster in enumerate(working):
            for second_index in range(first_index + 1, len(working)):
                combined = tuple(
                    sorted(
                        first_cluster + working[second_index],
                        key=lambda item: item.site_observation_id,
                    )
                )
                if not _cluster_is_compatible(
                    combined,
                    distances,
                    maximum_diameter_m,
                ):
                    continue
                diameter = _diameter(combined, distances)
                candidates.append(
                    (
                        diameter,
                        tuple(item.site_observation_id for item in combined),
                        first_index,
                        second_index,
                        combined,
                    )
                )
        if not candidates:
            break
        _, _, first_index, second_index, combined = min(candidates)
        working = [
            cluster
            for index, cluster in enumerate(working)
            if index not in {first_index, second_index}
        ]
        working.append(combined)
        working.sort(
            key=lambda cluster: tuple(item.site_observation_id for item in cluster)
        )

    output = [
        _finalise_canonical_site(cluster, distances, canonical_version)
        for cluster in working
    ]
    return tuple(sorted(output, key=lambda item: item.canonical_key))


def _cluster_is_compatible(
    members: tuple[CanonicalSiteEvidence, ...],
    distances: dict[tuple[str, str], float],
    maximum_diameter_m: float,
) -> bool:
    for index, first in enumerate(members):
        for second in members[index + 1 :]:
            if first.raw_upload_id == second.raw_upload_id:
                return False
            if not task_families_compatible(first.task_family, second.task_family):
                return False
            if (
                _distance(
                    first.site_observation_id,
                    second.site_observation_id,
                    distances,
                )
                > maximum_diameter_m
            ):
                return False
    return True


def _finalise_canonical_site(
    members: tuple[CanonicalSiteEvidence, ...],
    distances: dict[tuple[str, str], float],
    canonical_version: str,
) -> CanonicalSiteCandidate:
    representative = min(
        members,
        key=lambda candidate: (
            sum(
                _distance(
                    candidate.site_observation_id,
                    other.site_observation_id,
                    distances,
                )
                for other in members
            ),
            candidate.site_observation_id,
        ),
    )
    member_ids = tuple(item.site_observation_id for item in members)
    canonical_key = hashlib.sha256(
        (canonical_version + "\0" + "\0".join(member_ids)).encode("utf-8")
    ).hexdigest()
    task_families = {item.task_family for item in members}
    task_family = (
        task_families.pop()
        if len(task_families) == 1
        else representative.task_family
    )
    return CanonicalSiteCandidate(
        canonical_key=canonical_key,
        members=members,
        representative_site_observation_id=(
            representative.site_observation_id
        ),
        representative_position=representative.position,
        task_family=task_family,
        diameter_m=_diameter(members, distances),
        match_confidence=1.0 if len(members) == 1 else 0.88,
        match_reasons=(
            "Canonical Site members are task-compatible Site observations from distinct raw uploads.",
            "Every pair of member Site Points is within the configured maximum diameter.",
            "Polygon scope membership does not merge independent Sites.",
        ),
    )


def _diameter(
    members: tuple[CanonicalSiteEvidence, ...],
    distances: dict[tuple[str, str], float],
) -> float:
    return max(
        (
            _distance(
                first.site_observation_id,
                second.site_observation_id,
                distances,
            )
            for index, first in enumerate(members)
            for second in members[index + 1 :]
        ),
        default=0.0,
    )


def _distance(
    first_id: str,
    second_id: str,
    distances: dict[tuple[str, str], float],
) -> float:
    if first_id == second_id:
        return 0.0
    return distances[tuple(sorted((first_id, second_id)))]
