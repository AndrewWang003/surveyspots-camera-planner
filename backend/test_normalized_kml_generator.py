from datetime import datetime, timezone
import unittest
import xml.etree.ElementTree as ET

from app.kml_generator import (
    GENERATED_CAMERA_MYMAPS_STYLE_ID,
    _haversine_m,
    generate_normalized_request_kml,
)
from app.normalized_generation import (
    CameraSiteCoverage,
    GeneratedCameraLocation,
    GeneratedCanonicalSite,
    NormalizedGenerationOutput,
)


class NormalizedKmlGeneratorTests(unittest.TestCase):
    def test_shared_camera_cluster_is_emitted_once(self):
        statuses = {1: "complete", 2: "partial", 3: "requires_review"}
        sites = tuple(
            GeneratedCanonicalSite(
                canonical_site_id=f"site-{index}",
                name=f"Site {index}",
                lat=-42.0 + index / 1000,
                lng=147.0,
                request_sources=(f"Site {index}",),
                match_distance_m=1.0,
                required_camera_count=1,
                camera_count_source="historical_mode",
                selected_camera_count=1,
                missing_camera_count=0,
                generation_status=statuses[index],
                description_note="Required camera locations: 1. Reliable locations selected: 1.",
                icon_color="ff5b18c2" if index == 1 else "ff00eaff",
            )
            for index in (1, 2, 3)
        )
        camera = GeneratedCameraLocation(
            camera_location_cluster_id="cluster-1",
            representative_camera_observation_id="camera-1",
            name="Camera 1",
            lat=-42.0,
            lng=147.0,
            azimuth=None,
            historical_observation_count=2,
            latest_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            candidate_confidence=0.9,
            direction_positions=(
                (147.0001, -42.0001, 5.0),
                (147.0, -42.0, 6.0),
                (147.0002, -42.0002, 7.0),
            ),
            suggested_coverage_angle=75.0,
            coverages=[
                CameraSiteCoverage(
                    "site-1",
                    "Site 1",
                    "primary",
                    1,
                    site_icon_color="ff5b18c2",
                ),
                CameraSiteCoverage(
                    "site-2",
                    "Site 2",
                    "fallback",
                    1,
                    site_icon_color="ff00eaff",
                ),
            ],
        )

        xml = generate_normalized_request_kml(
            NormalizedGenerationOutput(sites, (camera,), ())
        )
        root = ET.fromstring(xml)
        names = [
            element.text
            for element in root.iter()
            if element.tag.endswith("name")
        ]

        self.assertEqual(names.count("Site 1 Cam 1"), 1)
        self.assertIn("Camera Locations", names)
        self.assertIn("Direction Lines", names)
        self.assertNotIn("Sites", names)
        self.assertNotIn("Site 1", names)
        self.assertNotIn("Site 2", names)
        self.assertNotIn("Site 3", names)
        self.assertEqual(names.count("Site 1 Cam 1 Direction"), 1)
        self.assertIn("Generated Camera Location.", xml)
        self.assertNotIn("Ranking evidence", xml)

        style_urls = {
            element.text
            for element in root.iter()
            if element.tag.endswith("styleUrl")
        }
        self.assertFalse(any(style_url.startswith("#site_") for style_url in style_urls))
        self.assertIn("#generated-camera-ff5b18c2", style_urls)
        style_map_ids = {
            element.attrib.get("id")
            for element in root.iter()
            if element.tag.endswith("StyleMap")
        }
        self.assertIn(GENERATED_CAMERA_MYMAPS_STYLE_ID, style_map_ids)
        self.assertIn("generated-camera-ff5b18c2", style_map_ids)
        self.assertIn(f"#{GENERATED_CAMERA_MYMAPS_STYLE_ID}-normal", xml)
        self.assertIn(f"#{GENERATED_CAMERA_MYMAPS_STYLE_ID}-highlight", xml)
        self.assertIn(
            "https://www.gstatic.com/mapspro/images/stock/503-wht-blank_maps.png",
            xml,
        )
        self.assertIn("ff000000", xml)
        self.assertIn("ff00ffff", xml)
        self.assertIn("<width>3.0</width>", xml)

        line_strings = [element for element in root.iter() if element.tag.endswith("LineString")]
        self.assertEqual(len(line_strings), 1)
        coordinates = next(
            element.text
            for element in line_strings[0].iter()
            if element.tag.endswith("coordinates")
        )
        output_positions = [
            tuple(float(value) for value in item.split(","))
            for item in coordinates.split()
        ]
        self.assertEqual(output_positions[1], (147.0, -42.0, 6.0))
        left_distance = _haversine_m(
            (output_positions[1][1], output_positions[1][0]),
            (output_positions[0][1], output_positions[0][0]),
        )
        right_distance = _haversine_m(
            (output_positions[1][1], output_positions[1][0]),
            (output_positions[2][1], output_positions[2][0]),
        )
        self.assertAlmostEqual(left_distance, right_distance, places=3)
        self.assertLess(
            output_positions[2][0],
            147.0002,
        )


if __name__ == "__main__":
    unittest.main()
