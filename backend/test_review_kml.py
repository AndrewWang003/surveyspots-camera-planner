import unittest
import xml.etree.ElementTree as ET

from app.routes.kml_routes import _generate_review_kml


class ReviewKmlTests(unittest.TestCase):
    def test_review_kml_preserves_camera_position_and_icon_style(self):
        kml = _generate_review_kml(
            {
                "source_file": "sample.kml",
                "items": [
                    {
                        "review_id": "C-001",
                        "item_type": "camera_site",
                        "reason": "Needs review.",
                        "camera_name": "NB Cam",
                        "title": "NB Cam",
                        "camera_task_family": "od",
                        "current_site_task_family": "od",
                        "current_site_name": "Station 10",
                        "lat": -42.1,
                        "lng": 147.2,
                        "style_definition": {
                            "icon_href": "https://example.com/camera.png",
                            "icon_color": "ff000000",
                        },
                        "candidate_sites": [],
                    }
                ],
            }
        )

        root = ET.fromstring(kml)
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        placemark = root.find(".//k:Placemark", ns)
        coordinates = placemark.findtext(".//k:coordinates", namespaces=ns)

        self.assertEqual(
            placemark.findtext("k:name", namespaces=ns),
            "REVIEW C-001 | NB Cam",
        )
        self.assertEqual(coordinates, "147.2,-42.1,0")
        self.assertIn("https://example.com/camera.png", kml)
        self.assertIn("ff000000", kml)


if __name__ == "__main__":
    unittest.main()
