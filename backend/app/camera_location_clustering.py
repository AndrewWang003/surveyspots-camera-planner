"""Deterministic complete-link clustering for historical Camera locations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .direction_geometry import Position, haversine_metres


@dataclass(frozen=True)
class CameraLocationEvidence:
    evidence_id: str
    position: Position


@dataclass(frozen=True)
class CameraLocationCluster:
    cluster_key: str
    members: tuple[CameraLocationEvidence, ...]
    representative_evidence_id: str
    representative_position: Position
    diameter_m: float


def build_camera_location_clusters(
    evidence: list[CameraLocationEvidence],
    maximum_diameter_m: float,
    cluster_version: str,
) -> tuple[CameraLocationCluster, ...]:
    """Cluster Camera evidence without transitive distance chaining.

    Clusters merge only when every member of the combined cluster remains
    within ``maximum_diameter_m`` of every other member.
    """
    if maximum_diameter_m <= 0:
        raise ValueError("maximum_diameter_m must be greater than zero")

    ordered = sorted(evidence, key=lambda item: item.evidence_id)
    if len({item.evidence_id for item in ordered}) != len(ordered):
        raise ValueError("Camera evidence IDs must be unique")

    distances = {
        (first.evidence_id, second.evidence_id): haversine_metres(
            first.position,
            second.position,
        )
        for index, first in enumerate(ordered)
        for second in ordered[index + 1 :]
    }
    working: list[tuple[CameraLocationEvidence, ...]] = [
        (item,) for item in ordered
    ]

    while True:
        merge_candidates: list[
            tuple[float, tuple[str, ...], int, int, tuple[CameraLocationEvidence, ...]]
        ] = []
        for first_index, first_cluster in enumerate(working):
            for second_index in range(first_index + 1, len(working)):
                combined = tuple(
                    sorted(
                        first_cluster + working[second_index],
                        key=lambda item: item.evidence_id,
                    )
                )
                diameter = _cluster_diameter(combined, distances)
                if diameter <= maximum_diameter_m:
                    merge_candidates.append(
                        (
                            diameter,
                            tuple(item.evidence_id for item in combined),
                            first_index,
                            second_index,
                            combined,
                        )
                    )
        if not merge_candidates:
            break

        _, _, first_index, second_index, combined = min(merge_candidates)
        working = [
            cluster
            for index, cluster in enumerate(working)
            if index not in {first_index, second_index}
        ]
        working.append(combined)
        working.sort(key=lambda cluster: tuple(item.evidence_id for item in cluster))

    clusters = [
        _finalise_cluster(cluster, distances, cluster_version) for cluster in working
    ]
    return tuple(sorted(clusters, key=lambda cluster: cluster.cluster_key))


def _finalise_cluster(
    members: tuple[CameraLocationEvidence, ...],
    distances: dict[tuple[str, str], float],
    cluster_version: str,
) -> CameraLocationCluster:
    representative = min(
        members,
        key=lambda candidate: (
            sum(
                _distance(candidate.evidence_id, other.evidence_id, distances)
                for other in members
            ),
            candidate.evidence_id,
        ),
    )
    member_ids = tuple(member.evidence_id for member in members)
    digest = hashlib.sha256(
        (cluster_version + "\0" + "\0".join(member_ids)).encode("utf-8")
    ).hexdigest()
    return CameraLocationCluster(
        cluster_key=digest,
        members=members,
        representative_evidence_id=representative.evidence_id,
        representative_position=representative.position,
        diameter_m=_cluster_diameter(members, distances),
    )


def _cluster_diameter(
    members: tuple[CameraLocationEvidence, ...],
    distances: dict[tuple[str, str], float],
) -> float:
    return max(
        (
            _distance(first.evidence_id, second.evidence_id, distances)
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
    key = tuple(sorted((first_id, second_id)))
    return distances[key]
