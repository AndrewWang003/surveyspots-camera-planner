from pathlib import Path
import unittest

from app.generation_request_parser import parse_generation_request_geometries
from private_fixtures import TEST_FIXTURES, read_private


KML_WITH_SITE_STYLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Style id="icon-1899-0288D1-nodesc-normal">
      <IconStyle><color>ffd18802</color></IconStyle>
    </Style>
    <StyleMap id="icon-1899-0288D1-nodesc">
      <Pair><key>normal</key><styleUrl>#icon-1899-0288D1-nodesc-normal</styleUrl></Pair>
    </StyleMap>
    <Folder>
      <name>OD Sites</name>
      <Placemark>
        <name>Station 7</name>
        <styleUrl>#icon-1899-0288D1-nodesc</styleUrl>
        <Point><coordinates>147.1,-42.1,0</coordinates></Point>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""


class GenerationRequestParserTests(unittest.TestCase):
    def test_fixture_is_parsed_without_treating_open_line_as_polygon(self):
        pins, polygons = parse_generation_request_geometries(
            read_private(TEST_FIXTURES / "CLtest.kml")
        )

        self.assertEqual(len(pins), 5)
        self.assertEqual(len(polygons), 1)
        self.assertEqual(len(polygons[0].outer_ring), 14)

    def test_site_icon_color_is_preserved_for_generation(self):
        pins, _polygons = parse_generation_request_geometries(KML_WITH_SITE_STYLE)

        self.assertEqual(pins[0].icon_color, "ffd18802")
        self.assertEqual(pins[0].task_family, "od")


if __name__ == "__main__":
    unittest.main()
