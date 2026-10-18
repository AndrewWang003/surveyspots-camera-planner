from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from app.normalized_generation import (
    _find_selected_candidate_row,
    _parse_direction_positions,
    choose_request_camera_count,
)


class NormalizedGenerationTests(unittest.TestCase):
    def test_request_explicit_count_has_highest_priority(self):
        rows = [
            {
                "required_camera_count": 3,
                "camera_count_source": "historical_mode",
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            }
        ]

        self.assertEqual(
            choose_request_camera_count(2, rows),
            (2, "request_explicit_description"),
        )

    def test_historical_mode_precedes_historical_explicit_fallback(self):
        rows = [
            {
                "required_camera_count": 2,
                "camera_count_source": "historical_mode",
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            },
            {
                "required_camera_count": 3,
                "camera_count_source": "explicit_description",
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            },
        ]

        self.assertEqual(choose_request_camera_count(None, rows), (2, "historical_mode"))

    def test_historical_mode_tie_uses_larger_count(self):
        rows = [
            {
                "required_camera_count": 2,
                "camera_count_source": "historical_mode",
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            },
            {
                "required_camera_count": 3,
                "camera_count_source": "historical_mode",
                "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            },
        ]

        self.assertEqual(choose_request_camera_count(None, rows), (3, "historical_mode"))

    def test_direction_positions_preserve_source_order_and_altitude(self):
        source = "[[147.1, -42.1, 5], [147.2, -42.2, 6], [147.3, -42.3, 7]]"

        self.assertEqual(
            _parse_direction_positions(source),
            (
                (147.1, -42.1, 5.0),
                (147.2, -42.2, 6.0),
                (147.3, -42.3, 7.0),
            ),
        )

    def test_invalid_direction_positions_are_not_emitted(self):
        self.assertEqual(_parse_direction_positions("[[147.1]]"), ())
        self.assertEqual(_parse_direction_positions("not-json"), ())

    def test_selected_row_uses_the_winning_source_relationship(self):
        rows = [
            {
                "cluster_id": "cluster-1",
                "selection_tier": "primary",
                "relationship_id": "relationship-loser",
                "candidate_confidence": 0.99,
                "distance_to_site_m": 10.0,
                "representative_camera_observation_id": "camera-1",
            },
            {
                "cluster_id": "cluster-1",
                "selection_tier": "primary",
                "relationship_id": "relationship-winner",
                "candidate_confidence": 0.90,
                "distance_to_site_m": 5.0,
                "representative_camera_observation_id": "camera-1",
            },
        ]
        selection = SimpleNamespace(
            camera_location_cluster_id="cluster-1",
            selection_tier="primary",
            source_relationship_id="relationship-winner",
        )

        selected = _find_selected_candidate_row(rows, selection)

        self.assertEqual(selected["relationship_id"], "relationship-winner")


if __name__ == "__main__":
    unittest.main()
