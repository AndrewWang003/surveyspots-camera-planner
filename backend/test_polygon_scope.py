from pathlib import Path
import unittest

from app.business_classifier import classify_raw_placemarks
from app.polygon_scope import build_polygon_scope_bundle
from app.raw_placemark_parser import extract_raw_placemarks
from private_fixtures import TEST_FIXTURES, read_private




class PolygonScopeTests(unittest.TestCase):
    def test_cltest_preserves_real_polygon_and_unpersisted_line_handles(self):
        path = TEST_FIXTURES / "CLtest.kml"
        raw = extract_raw_placemarks(read_private(path))
        decisions = classify_raw_placemarks(raw, path.name)
        bundle = build_polygon_scope_bundle(raw, decisions)
        line = next(item for item in raw if item.placemark_index == 5)
        polygon = bundle.polygons[0]

        line_geometry = next(g for g in line.geometry if g["type"] == "LineString")
        self.assertEqual(len(line_geometry["coordinate_sets"][0]["positions"]), 3)
        self.assertTrue(polygon.is_closed)
        self.assertEqual(polygon.distinct_outer_vertex_count, 13)
        self.assertEqual(polygon.scope_status, "unbound_review")
        self.assertFalse(bundle.memberships)
        self.assertEqual(polygon.style_evidence["line_width"], "1.2")
        self.assertEqual(polygon.style_evidence["poly_fill"], "1")

    def test_polygon_with_multiple_sites_creates_multiple_memberships(self):
        kml = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
        <Folder><name>IC Sites</name>
          <Placemark><name>Site 1</name><Point><coordinates>0.25,0.25,0</coordinates></Point></Placemark>
          <Placemark><name>Site 2</name><Point><coordinates>0.75,0.75,0</coordinates></Point></Placemark>
          <Placemark><name>Site 3</name><Point><coordinates>2,2,0</coordinates></Point></Placemark>
          <Placemark><name>Work Area</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
            0,0,0 1,0,0 1,1,0 0,1,0 0,0,0
          </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
        </Folder></Document></kml>"""
        raw = extract_raw_placemarks(kml)
        decisions = classify_raw_placemarks(raw, "scope.kml")

        bundle = build_polygon_scope_bundle(raw, decisions)

        self.assertEqual(bundle.polygons[0].scope_status, "accepted_site_scope")
        self.assertEqual(len(bundle.memberships), 2)


if __name__ == "__main__":
    unittest.main()
