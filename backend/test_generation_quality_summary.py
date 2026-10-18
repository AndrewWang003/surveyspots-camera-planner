from datetime import datetime, timezone
import unittest

from app.normalized_generation import (
    CameraSiteCoverage,
    GeneratedCameraLocation,
    GeneratedCanonicalSite,
    NormalizedGenerationOutput,
)
from app.routes.kml_routes import _generation_quality_counts


def _site(site_id: str, status: str) -> GeneratedCanonicalSite:
    return GeneratedCanonicalSite(
        canonical_site_id=site_id,
        name=site_id,
        lat=-42.0,
        lng=147.0,
        request_sources=(site_id,),
        match_distance_m=0.0,
        required_camera_count=1,
        camera_count_source="historical_mode",
        selected_camera_count=1 if status == "complete" else 0,
        missing_camera_count=0 if status == "complete" else 1,
        generation_status=status,
        description_note="Generation summary test.",
    )


def _camera(cluster_id: str, roles: tuple[str, ...]) -> GeneratedCameraLocation:
    return GeneratedCameraLocation(
        camera_location_cluster_id=cluster_id,
        representative_camera_observation_id=f"observation-{cluster_id}",
        name=cluster_id,
        lat=-42.0,
        lng=147.0,
        azimuth=None,
        historical_observation_count=1,
        latest_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        candidate_confidence=0.9,
        coverages=[
            CameraSiteCoverage(
                canonical_site_id=f"site-{index}",
                site_name=f"Site {index}",
                selection_tier="primary",
                selection_rank=index,
                layout_role=role,
            )
            for index, role in enumerate(roles, start=1)
        ],
    )


class GenerationQualitySummaryTests(unittest.TestCase):
    def test_counts_site_statuses_and_unique_supplemental_locations(self):
        output = NormalizedGenerationOutput(
            sites=(
                _site("site-1", "complete"),
                _site("site-2", "partial"),
                _site("site-3", "requires_review"),
            ),
            cameras=(
                _camera("camera-1", ("base_layout",)),
                _camera(
                    "camera-2",
                    ("base_layout", "supplemental_historical"),
                ),
            ),
            unmatched_request_items=("Unmatched Pin",),
        )

        self.assertEqual(
            _generation_quality_counts(output),
            {
                "site_count": 3,
                "complete_count": 1,
                "partial_count": 1,
                "requires_review_count": 1,
                "camera_count": 2,
                "supplemental_camera_count": 1,
                "unmatched_count": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
