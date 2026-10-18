import unittest

from fastapi import HTTPException

from app.models import LayerSelection
from app.preview_cache import PreviewFileData
from app.raw_placemark_parser import extract_raw_placemarks
from app.routes.kml_routes import _build_file_preview, _resolve_preview_selections


def preview_file(file_id, filename, layers):
    return PreviewFileData(
        filename=filename,
        content=b"<kml/>",
        content_type="application/vnd.google-earth.kml+xml",
        kml_text="<kml/>",
        points=[],
        file_id=file_id,
        layer_names=tuple(layers),
    )


class UploadLayerReviewTests(unittest.TestCase):
    def test_preview_includes_non_point_geometry_layers(self):
        placemarks = extract_raw_placemarks(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Folder><name>Direction Lines</name><Placemark><name>Line 1</name>
            <LineString><coordinates>147,-42,0 147.1,-42.1,0</coordinates></LineString>
            </Placemark></Folder></Document></kml>"""
        )

        preview = _build_file_preview(
            "file-1",
            "directions.kml",
            [],
            placemarks,
        )

        self.assertEqual(preview.total_points, 0)
        self.assertEqual(preview.total_placemarks, 1)
        self.assertEqual(preview.layers[0].layer_name, "Direction Lines")
        self.assertEqual(preview.layers[0].geometry_types, ["LineString"])
        self.assertEqual(
            preview.layers[0].business_role_counts,
            {"camera_direction": 1},
        )

    def test_atc_layer_preview_uses_atc_family_and_roles(self):
        placemarks = extract_raw_placemarks(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Folder><name>ATC</name>
            <Placemark><name>ATC 1</name><Point><coordinates>147,-42,0</coordinates></Point></Placemark>
            <Placemark><name>Tube 1</name><LineString><coordinates>147,-42,0 147.1,-42.1,0</coordinates></LineString></Placemark>
            </Folder></Document></kml>"""
        )

        preview = _build_file_preview("file-1", "atc.kml", [], placemarks)

        self.assertEqual(preview.layers[0].inferred_family, "atc")
        self.assertEqual(
            preview.layers[0].business_role_counts,
            {"atc_location": 1, "atc_tube_line": 1},
        )

    def test_same_layer_name_is_isolated_by_preview_file_id(self):
        files = [
            preview_file("file-1", "first.kml", ["Sites"]),
            preview_file("file-2", "second.kml", ["Sites"]),
        ]
        selections = [
            LayerSelection(
                file_id="file-1",
                filename="first.kml",
                layer_name="Sites",
                selected_family="ic",
            ),
            LayerSelection(
                file_id="file-2",
                filename="second.kml",
                layer_name="Sites",
                selected_family="od",
            ),
        ]

        resolved = _resolve_preview_selections(files, selections)

        self.assertEqual(resolved["file-1"]["Sites"].selected_family, "ic")
        self.assertEqual(resolved["file-2"]["Sites"].selected_family, "od")

    def test_review_tags_are_resolved_with_layer_selection(self):
        files = [
            preview_file("file-1", "first.kml", ["IC"]),
        ]
        selections = [
            LayerSelection(
                file_id="file-1",
                filename="first.kml",
                layer_name="IC",
                selected_family="ic",
                review_tags=["site", "camera", "direction"],
            ),
        ]

        resolved = _resolve_preview_selections(files, selections)

        self.assertEqual(resolved["file-1"]["IC"].selected_family, "ic")
        self.assertEqual(
            resolved["file-1"]["IC"].review_tags,
            ("site", "camera", "direction"),
        )

    def test_ambiguous_legacy_selection_is_rejected(self):
        files = [
            preview_file("file-1", "first.kml", ["Sites"]),
            preview_file("file-2", "second.kml", ["Sites"]),
        ]
        selections = [LayerSelection(layer_name="Sites", selected_family="ic")]

        with self.assertRaises(HTTPException) as context:
            _resolve_preview_selections(files, selections)

        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
