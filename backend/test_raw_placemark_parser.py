import unittest

from app.raw_placemark_parser import extract_raw_placemarks


KML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Style id="camera-style">
      <IconStyle><color>ffffffff</color><Icon><href>camera.png</href></Icon></IconStyle>
    </Style>
    <Folder>
      <name>IC / Cameras</name>
      <Placemark>
        <name>Cam 1</name>
        <description>Historical camera</description>
        <styleUrl>#camera-style</styleUrl>
        <ExtendedData><Data name="camera_count"><value>2</value></Data></ExtendedData>
        <Point><coordinates>147.1000,-42.8000,5</coordinates></Point>
      </Placemark>
      <Placemark>
        <name>Direction 1</name>
        <LineString>
          <coordinates>
            147.0999,-42.7999,0 147.1000,-42.8000,0 147.1001,-42.7999,0
          </coordinates>
        </LineString>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""


class RawPlacemarkParserTests(unittest.TestCase):
    def test_preserves_context_style_extended_data_and_geometry(self):
        records = extract_raw_placemarks(KML_SAMPLE)

        self.assertEqual(len(records), 2)
        camera = records[0]
        self.assertEqual(camera.folder_path, ("IC / Cameras",))
        self.assertEqual(camera.style_id, "camera-style")
        self.assertEqual(camera.style_definition["icon_href"], "camera.png")
        self.assertEqual(camera.extended_data, {"camera_count": "2"})
        self.assertEqual(camera.geometry_types, ("Point",))
        self.assertEqual(
            camera.geometry[0]["coordinate_sets"][0]["positions"][0],
            [147.1, -42.8, 5.0],
        )
        self.assertIn("Placemark", camera.raw_kml_fragment)

    def test_preserves_three_point_linestring_in_source_order(self):
        direction = extract_raw_placemarks(KML_SAMPLE)[1]
        positions = direction.geometry[0]["coordinate_sets"][0]["positions"]

        self.assertEqual(direction.geometry_types, ("LineString",))
        self.assertEqual(len(positions), 3)
        self.assertEqual(positions[1], [147.1, -42.8, 0.0])


if __name__ == "__main__":
    unittest.main()
