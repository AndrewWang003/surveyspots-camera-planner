import unittest

from app.preview_cache import PreviewFileData


class PreviewCacheTests(unittest.TestCase):
    def test_preview_retains_source_until_confirm_without_database_identity(self):
        preview = PreviewFileData(
            filename="history.kml",
            content=b"<kml/>",
            content_type="application/vnd.google-earth.kml+xml",
            kml_text="<kml/>",
            points=[],
        )

        self.assertEqual(preview.content, b"<kml/>")
        self.assertFalse(hasattr(preview, "raw_upload_id"))


if __name__ == "__main__":
    unittest.main()
