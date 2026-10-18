"""Choose a frequent project-level Site layout and supplemental Cameras."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import os
import re
from typing import Any, Mapping


# Job-number prefix used in survey file names, e.g. "JOB1234 Main St.kml".
# Set SURVEYSPOTS_PROJECT_ID_PREFIX to match your organisation's naming.
PROJECT_ID_PREFIX = os.environ.get("SURVEYSPOTS_PROJECT_ID_PREFIX", "JOB")
PROJECT_ID_RE = re.compile(rf"\b{re.escape(PROJECT_ID_PREFIX)}\s*[-_ ]?\s*(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SiteLayoutPreference:
    """Frequency result for one Canonical Site across independent projects."""

    base_cluster_ids: frozenset[str]
    base_layout_frequency: int
    project_vote_count: int
    distinct_layout_count: int
    cluster_frequencies: dict[str, int]

    def role_for_cluster(self, cluster_id: str, selection_tier: str) -> str:
        if not self.base_cluster_ids:
            return "ungrouped_historical"
        if selection_tier == "primary" and cluster_id in self.base_cluster_ids:
            return "base_layout"
        return "supplemental_historical"


@dataclass(frozen=True)
class _ProjectLayoutVote:
    project_key: str
    layout_key: str
    cluster_ids: frozenset[str]
    observed_at: datetime


def select_site_layout_preference(
    candidate_rows: list[Mapping[str, Any]],
) -> SiteLayoutPreference:
    """Select the most frequent non-empty Primary layout, one vote per project."""
    clusters_by_layout: dict[str, set[str]] = defaultdict(set)
    layout_metadata: dict[str, tuple[str, datetime]] = {}
    for row in candidate_rows:
        if row["selection_tier"] != "primary" or row["relationship_id"] is None:
            continue
        raw_upload_id = _optional_text(row, "layout_raw_upload_id")
        site_observation_id = _optional_text(row, "layout_site_observation_id")
        source_file = _optional_text(row, "layout_source_file")
        observed_at = _row_value(row, "layout_uploaded_at")
        if not raw_upload_id or not site_observation_id or not isinstance(
            observed_at, datetime
        ):
            continue
        layout_key = f"{raw_upload_id}:{site_observation_id}"
        project_key = project_key_from_source(source_file, raw_upload_id)
        clusters_by_layout[layout_key].add(str(row["cluster_id"]))
        layout_metadata[layout_key] = (project_key, observed_at)

    votes = [
        _ProjectLayoutVote(
            project_key=layout_metadata[layout_key][0],
            layout_key=layout_key,
            cluster_ids=frozenset(cluster_ids),
            observed_at=layout_metadata[layout_key][1],
        )
        for layout_key, cluster_ids in clusters_by_layout.items()
        if cluster_ids
    ]

    latest_vote_by_project: dict[str, _ProjectLayoutVote] = {}
    for vote in votes:
        existing = latest_vote_by_project.get(vote.project_key)
        if existing is None or (vote.observed_at, vote.layout_key) > (
            existing.observed_at,
            existing.layout_key,
        ):
            latest_vote_by_project[vote.project_key] = vote
    independent_votes = tuple(latest_vote_by_project.values())
    if not independent_votes:
        return SiteLayoutPreference(frozenset(), 0, 0, 0, {})

    signature_counts = Counter(vote.cluster_ids for vote in independent_votes)
    first_seen = {
        signature: min(
            vote.observed_at
            for vote in independent_votes
            if vote.cluster_ids == signature
        )
        for signature in signature_counts
    }
    base_signature = min(
        signature_counts,
        key=lambda signature: (
            -signature_counts[signature],
            first_seen[signature],
            tuple(sorted(signature)),
        ),
    )
    cluster_frequencies = Counter(
        cluster_id
        for vote in independent_votes
        for cluster_id in vote.cluster_ids
    )
    return SiteLayoutPreference(
        base_cluster_ids=base_signature,
        base_layout_frequency=signature_counts[base_signature],
        project_vote_count=len(independent_votes),
        distinct_layout_count=len(signature_counts),
        cluster_frequencies=dict(cluster_frequencies),
    )


def project_key_from_source(source_file: str, raw_upload_id: str) -> str:
    """Use a job-number project identity when available; otherwise keep one upload vote."""
    match = PROJECT_ID_RE.search(source_file)
    if match:
        return f"project:{int(match.group(1))}"
    return f"upload:{raw_upload_id}"


def _row_value(row: Mapping[str, Any], key: str) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        return None


def _optional_text(row: Mapping[str, Any], key: str) -> str:
    value = _row_value(row, key)
    return str(value) if value is not None else ""
