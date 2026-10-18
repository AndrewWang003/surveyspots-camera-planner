import unittest

from app.camera_generation_selector import (
    CameraGenerationCandidate,
    select_camera_locations,
)


def candidate(
    cluster_id,
    tier,
    distance,
    confidence=0.9,
    historical_count=1,
    relationship_status="suggested",
    cluster_status="suggested",
    layout_role="ungrouped_historical",
    layout_frequency=0,
    represented_camera_count=1,
):
    return CameraGenerationCandidate(
        camera_location_cluster_id=cluster_id,
        representative_camera_observation_id=f"camera-{cluster_id}",
        selection_tier=tier,
        distance_to_site_m=distance,
        candidate_confidence=confidence,
        source_relationship_id=f"relationship-{cluster_id}",
        historical_observation_count=historical_count,
        relationship_status=relationship_status,
        cluster_status=cluster_status,
        layout_role=layout_role,
        layout_frequency=layout_frequency,
        represented_camera_count=represented_camera_count,
    )


class CameraGenerationSelectorTests(unittest.TestCase):
    def test_selects_primary_then_fallback_then_nearby(self):
        result = select_camera_locations(
            "site-1",
            3,
            [
                candidate("nearby", "nearby_historical", 2.0),
                candidate("fallback", "fallback", 20.0),
                candidate("primary", "primary", 30.0),
            ],
        )

        self.assertEqual(
            [item.selection_tier for item in result.selected_locations],
            ["primary", "fallback", "nearby_historical"],
        )

    def test_same_location_cluster_is_selected_only_once(self):
        result = select_camera_locations(
            "site-1",
            2,
            [
                candidate("same", "fallback", 10.0),
                candidate("same", "primary", 12.0),
                candidate("other", "fallback", 20.0),
            ],
        )

        self.assertEqual(len(result.selected_locations), 2)
        self.assertEqual(result.selected_locations[0].selection_tier, "primary")
        self.assertEqual(
            len({item.camera_location_cluster_id for item in result.selected_locations}),
            2,
        )

    def test_shortage_is_written_in_english(self):
        result = select_camera_locations(
            "site-1",
            2,
            [candidate("primary", "primary", 10.0)],
        )

        self.assertEqual(result.generation_status, "partial")
        self.assertEqual(result.missing_reliable_location_count, 1)
        self.assertEqual(
            result.site_description_note,
            "Reliable camera coverage available: 1 of 2 cameras across 1 location. Missing 1 reliable camera.",
        )

    def test_one_location_can_represent_multiple_cameras(self):
        result = select_camera_locations(
            "site-1",
            2,
            [
                candidate(
                    "shared-location",
                    "primary",
                    10.0,
                    represented_camera_count=2,
                )
            ],
        )

        self.assertEqual(result.generation_status, "complete")
        self.assertEqual(result.selected_camera_count, 2)
        self.assertEqual(len(result.selected_locations), 1)
        self.assertEqual(
            result.site_description_note,
            "Required cameras: 2. Reliable camera coverage selected: 2 across 1 location.",
        )

    def test_missing_count_requires_review_without_candidates(self):
        result = select_camera_locations("site-1", None, [])

        self.assertEqual(result.generation_status, "requires_review")
        self.assertFalse(result.selected_locations)
        self.assertTrue(result.requires_review)

    def test_weighted_score_ranks_candidates_within_the_same_tier(self):
        result = select_camera_locations(
            "site-1",
            1,
            [
                candidate("far", "primary", 200.0, confidence=0.90),
                candidate(
                    "near-supported",
                    "primary",
                    5.0,
                    confidence=0.85,
                    historical_count=5,
                ),
            ],
        )

        selected = result.selected_locations[0]
        self.assertEqual(selected.camera_location_cluster_id, "near-supported")
        self.assertEqual(selected.ranking_method, "weighted_evidence_v1")
        self.assertGreater(selected.ranking_score, 0.8)
        self.assertTrue(selected.ranking_reasons)

    def test_human_confirmation_precedes_automatic_weighted_score(self):
        result = select_camera_locations(
            "site-1",
            1,
            [
                candidate("automatic", "primary", 1.0, confidence=1.0),
                candidate(
                    "confirmed",
                    "primary",
                    200.0,
                    confidence=0.5,
                    relationship_status="human_confirmed",
                ),
            ],
        )

        self.assertEqual(
            result.selected_locations[0].camera_location_cluster_id,
            "confirmed",
        )

    def test_base_layout_precedes_supplemental_camera_in_same_tier(self):
        result = select_camera_locations(
            "site-1",
            2,
            [
                candidate(
                    "supplemental",
                    "primary",
                    1.0,
                    confidence=1.0,
                    layout_role="supplemental_historical",
                    layout_frequency=2,
                ),
                candidate(
                    "base-1",
                    "primary",
                    30.0,
                    confidence=0.8,
                    layout_role="base_layout",
                    layout_frequency=3,
                ),
                candidate(
                    "base-2",
                    "primary",
                    40.0,
                    confidence=0.8,
                    layout_role="base_layout",
                    layout_frequency=3,
                ),
            ],
        )

        self.assertEqual(
            [item.camera_location_cluster_id for item in result.selected_locations],
            ["base-1", "base-2"],
        )


if __name__ == "__main__":
    unittest.main()
