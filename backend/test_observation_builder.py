import math
from pathlib import Path
import unittest

from app.business_classifier import classify_raw_placemarks
from app.direction_geometry import (
    analyse_three_point_direction,
    circular_distance_degrees,
)
from app.observation_builder import build_observation_bundle
from app.raw_placemark_parser import extract_raw_placemarks
from private_fixtures import TESTDATA, read_private, private_corpus




def destination(bearing: float, distance_degrees: float = 0.001):
    radians = math.radians(bearing)
    return (
        math.sin(radians) * distance_degrees,
        math.cos(radians) * distance_degrees,
        0.0,
    )


class DirectionGeometryTests(unittest.TestCase):
    def test_circular_midpoint_handles_north_wraparound(self):
        anchor = (0.0, 0.0, 0.0)
        analysis = analyse_three_point_direction(
            (destination(350), anchor, destination(10))
        )

        self.assertIsNotNone(analysis)
        self.assertLess(
            circular_distance_degrees(analysis.suggested_centre_bearing, 0.0),
            0.01,
        )
        self.assertAlmostEqual(analysis.suggested_coverage_angle, 20.0, places=3)

    def test_circular_midpoint_handles_south_facing_direction(self):
        anchor = (0.0, 0.0, 0.0)
        analysis = analyse_three_point_direction(
            (destination(170), anchor, destination(190))
        )

        self.assertIsNotNone(analysis)
        self.assertLess(
            circular_distance_degrees(analysis.suggested_centre_bearing, 180.0),
            0.01,
        )


class ObservationBuilderTests(unittest.TestCase):
    def test_full_corpus_builds_expected_observations_and_bindings(self):
        totals = {
            "cameras": 0,
            "directions": 0,
            "automatic": 0,
            "review": 0,
            "nonstandard": 0,
        }
        for path in private_corpus():
            raw = extract_raw_placemarks(read_private(path))
            decisions = classify_raw_placemarks(raw, path.name)
            bundle = build_observation_bundle(raw, decisions)
            totals["cameras"] += len(bundle.cameras)
            totals["directions"] += len(bundle.directions)
            totals["automatic"] += sum(
                binding.binding_status == "automatic" for binding in bundle.bindings
            )
            totals["review"] += sum(
                binding.binding_status == "review_candidate"
                for binding in bundle.bindings
            )
            totals["nonstandard"] += sum(
                direction.geometry_status == "nonstandard_review"
                for direction in bundle.directions
            )

        standard_directions = totals["directions"] - totals["nonstandard"]
        self.assertGreater(totals["cameras"], 0)
        self.assertEqual(
            totals["automatic"] + totals["review"],
            standard_directions,
        )

    def test_automatic_binding_uses_point_two_without_modifying_source(self):
        path = TESTDATA / "survey-c.kml"
        raw = extract_raw_placemarks(read_private(path))
        decisions = classify_raw_placemarks(raw, path.name)
        bundle = build_observation_bundle(raw, decisions)

        self.assertEqual(len(bundle.bindings), 2)
        self.assertTrue(all(binding.is_selected for binding in bundle.bindings))
        direction = next(item for item in bundle.directions if item.name == "Line 3")
        self.assertEqual(direction.analysis.camera_anchor, direction.positions[1])
        self.assertEqual(len(direction.positions), 3)

    def test_missing_camera_icons_are_derived_from_direction_point_two(self):
        path = TESTDATA / "survey-g-bicycle.kml"
        raw = extract_raw_placemarks(read_private(path))
        decisions = classify_raw_placemarks(raw, path.name)
        bundle = build_observation_bundle(raw, decisions)

        derived = [
            camera
            for camera in bundle.cameras
            if camera.location_method == "direction_point_2"
        ]
        self.assertEqual(len(derived), 4)
        directions_by_index = {
            direction.placemark_index: direction for direction in bundle.directions
        }
        for camera in derived:
            direction = directions_by_index[camera.placemark_index]
            self.assertEqual(camera.position, direction.positions[1])
            self.assertEqual(direction.binding_state, "automatic")
            self.assertFalse(direction.requires_review)


if __name__ == "__main__":
    unittest.main()
