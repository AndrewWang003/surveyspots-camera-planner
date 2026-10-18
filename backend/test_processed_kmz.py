import unittest
import zipfile
import io

from app.routes.kml_routes import _build_reference_kmz


class ProcessedKmzTests(unittest.TestCase):
    def test_processed_kmz_contains_only_generated_kml(self):
        content = _build_reference_kmz("<kml></kml>")
        with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
            self.assertEqual(archive.namelist(), ["doc.kml"])
            self.assertEqual(archive.read("doc.kml"), b"<kml></kml>")


if __name__ == "__main__":
    unittest.main()
