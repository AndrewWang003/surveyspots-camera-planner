import unittest

from app.business_classifier import classify_raw_placemarks
from app.historical_layer_review import (
    LayerReviewSelection,
    apply_layer_review_overrides,
    filter_selected_placemarks,
    layer_path_for_placemark,
    validate_selected_layers,
)
from app.raw_placemark_parser import extract_raw_placemarks


KML = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Folder><name>Reviewed Layer</name>
  <Placemark><name>Site 1</name><Point><coordinates>147,-42,0</coordinates></Point></Placemark>
  <Placemark><name>Camera 1</name><Point><coordinates>147.1,-42.1,0</coordinates></Point></Placemark>
</Folder>
<Folder><name>Excluded Layer</name>
  <Placemark><name>Site 2</name><Point><coordinates>148,-43,0</coordinates></Point></Placemark>
</Folder>
</Document></kml>"""


class HistoricalLayerReviewTests(unittest.TestCase):
    def test_only_selected_layer_produces_normalized_placemarks(self):
        placemarks = extract_raw_placemarks(KML)

        selected = filter_selected_placemarks(
            placemarks,
            {"Reviewed Layer": "od"},
        )

        self.assertEqual(len(placemarks), 3)
        self.assertEqual(len(selected), 2)
        self.assertEqual(
            {layer_path_for_placemark(item) for item in selected},
            {"Reviewed Layer"},
        )

    def test_reviewed_task_family_is_applied_with_provenance(self):
        placemarks = filter_selected_placemarks(
            extract_raw_placemarks(KML),
            {"Reviewed Layer": "od"},
        )
        decisions = classify_raw_placemarks(placemarks, "history.kml")

        reviewed = apply_layer_review_overrides(
            placemarks,
            decisions,
            {"Reviewed Layer": "od"},
        )

        self.assertEqual({decision.task_family for decision in reviewed}, {"od"})
        self.assertTrue(
            all(
                decision.classification_method
                == "rule_engine_with_human_layer_review"
                for decision in reviewed
            )
        )
        self.assertEqual(
            reviewed[0].decision_metadata["human_layer_review"]["selected_family"],
            "od",
        )

    def test_review_tags_are_applied_with_provenance(self):
        placemarks = filter_selected_placemarks(
            extract_raw_placemarks(KML),
            {
                "Reviewed Layer": LayerReviewSelection(
                    selected_family="ic",
                    review_tags=("site", "camera", "direction"),
                )
            },
        )
        decisions = classify_raw_placemarks(placemarks, "history.kml")

        reviewed = apply_layer_review_overrides(
            placemarks,
            decisions,
            {
                "Reviewed Layer": LayerReviewSelection(
                    selected_family="ic",
                    review_tags=("site", "camera", "direction"),
                )
            },
        )

        self.assertEqual(
            reviewed[0].decision_metadata["human_layer_review"]["review_tags"],
            ["site", "camera", "direction"],
        )
        self.assertIn("Allowed data tags", reviewed[0].classification_reasons[-1])

    def test_single_camera_tag_can_disambiguate_point_layer(self):
        kml = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
        <Folder><name>Unclear</name>
          <Placemark><name>Location 1</name><Point><coordinates>147,-42,0</coordinates></Point></Placemark>
        </Folder></Document></kml>"""
        placemarks = extract_raw_placemarks(kml)
        decisions = classify_raw_placemarks(placemarks, "history.kml")

        reviewed = apply_layer_review_overrides(
            placemarks,
            decisions,
            {
                "Unclear": LayerReviewSelection(
                    selected_family="ic",
                    review_tags=("camera",),
                )
            },
        )

        self.assertEqual(reviewed[0].business_role, "camera_location")

    def test_site_layer_override_does_not_relabel_explicit_camera(self):
        placemarks = filter_selected_placemarks(
            extract_raw_placemarks(KML),
            {"Reviewed Layer": "site"},
        )
        decisions = classify_raw_placemarks(placemarks, "history.kml")

        reviewed = apply_layer_review_overrides(
            placemarks,
            decisions,
            {"Reviewed Layer": "site"},
        )
        roles_by_index = {
            decision.placemark_index: decision.business_role for decision in reviewed
        }

        self.assertEqual(roles_by_index[0], "site")
        self.assertEqual(roles_by_index[1], "camera_location")

    def test_atc_is_a_supported_reviewed_task_family(self):
        validate_selected_layers({"ATC": "atc"})

    def test_unknown_review_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_selected_layers(
                {
                    "Mixed": LayerReviewSelection(
                        selected_family="ic",
                        review_tags=("bad-tag",),
                    )
                }
            )


if __name__ == "__main__":
    unittest.main()
