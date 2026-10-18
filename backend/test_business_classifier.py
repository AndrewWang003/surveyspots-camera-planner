import unittest
from pathlib import Path

from app.business_classifier import (
    classify_raw_placemarks,
    extract_required_camera_count,
)
from app.raw_placemark_parser import extract_raw_placemarks
from private_fixtures import TESTDATA, read_private




class BusinessClassifierTests(unittest.TestCase):
    def _classify(self, filename):
        path = TESTDATA / filename
        raw = extract_raw_placemarks(read_private(path))
        decisions = classify_raw_placemarks(raw, filename)
        return raw, {decision.placemark_index: decision for decision in decisions}

    def test_extracts_explicit_site_camera_requirement(self):
        self.assertEqual(extract_required_camera_count("2 cameras"), 2)
        self.assertEqual(extract_required_camera_count("Install 3 x cams"), 3)
        self.assertIsNone(extract_required_camera_count("Camera 2 location"))

    def test_ic_points_are_sites_not_cameras(self):
        raw, decisions = self._classify("survey-a.kml")
        ic_points = [
            placemark
            for placemark in raw
            if placemark.folder_path == ("IC",) and "Point" in placemark.geometry_types
        ]

        self.assertEqual(len(ic_points), 35)
        self.assertTrue(
            all(decisions[placemark.placemark_index].business_role == "site" for placemark in ic_points)
        )
        explicit_counts = [
            decisions[placemark.placemark_index].required_camera_count
            for placemark in ic_points
            if decisions[placemark.placemark_index].required_camera_count is not None
        ]
        self.assertEqual(len(explicit_counts), 14)
        self.assertTrue(all(count == 2 for count in explicit_counts))

    def test_same_icon_context_separates_od_station_from_camera(self):
        raw, decisions = self._classify("survey-b-od-cameras.kml")
        station = next(
            placemark
            for placemark in raw
            if placemark.folder_path == ("OD- Highway A",)
            and placemark.placemark_name.startswith("Station 1")
        )
        camera = next(
            placemark
            for placemark in raw
            if placemark.folder_path == ("OD Cameras (Blue)",)
            and placemark.placemark_name == "WB Cam"
            and "Point" in placemark.geometry_types
        )

        self.assertEqual(decisions[station.placemark_index].business_role, "site")
        self.assertEqual(decisions[camera.placemark_index].business_role, "camera_location")

    def test_three_point_camera_line_and_route_are_separated(self):
        raw, decisions = self._classify("survey-a.kml")
        route_records = [p for p in raw if p.folder_path == ("Route 1",)]
        self.assertTrue(route_records)
        self.assertTrue(
            all(decisions[p.placemark_index].business_role == "route" for p in route_records)
        )

        raw, decisions = self._classify("survey-c.kml")
        direction = next(p for p in raw if p.placemark_name == "Line 3")
        self.assertEqual(decisions[direction.placemark_index].business_role, "camera_direction")

    def test_site_prefixed_camera_name_uses_camera_group_context(self):
        raw, decisions = self._classify("survey-d.kml")
        camera = next(
            p for p in raw if p.placemark_name.startswith("Site 4.1 & 4.2- Cam 2")
        )
        self.assertEqual(decisions[camera.placemark_index].business_role, "camera_location")

    def test_nonstandard_camera_line_is_preserved_for_review(self):
        raw, decisions = self._classify("survey-e.kml")
        direction = next(p for p in raw if p.placemark_name == "Line 5")
        decision = decisions[direction.placemark_index]
        self.assertEqual(decision.business_role, "camera_direction")
        self.assertLess(decision.classification_confidence, 0.8)

    def test_atc_layer_is_preserved_without_entering_camera_roles(self):
        raw, decisions = self._classify("survey-f-atc.kml")
        atc_records = [placemark for placemark in raw if placemark.folder_path == ("ATC",)]

        self.assertEqual(len(atc_records), 3)
        roles = [decisions[placemark.placemark_index].business_role for placemark in atc_records]
        self.assertEqual(roles.count("atc_location"), 1)
        self.assertEqual(roles.count("atc_tube_line"), 2)
        self.assertTrue(
            all(decisions[placemark.placemark_index].task_family == "atc" for placemark in atc_records)
        )


if __name__ == "__main__":
    unittest.main()
