from pathlib import Path
from dataclasses import replace
import unittest

from app.business_classifier import classify_raw_placemarks
from app.camera_site_relationships import (
    build_camera_site_relationship_bundle,
    named_site_keys_from_camera,
)
from app.historical_layer_review import (
    LayerReviewSelection,
    apply_layer_review_overrides,
    filter_selected_placemarks,
)
from app.observation_builder import build_observation_bundle
from app.raw_placemark_parser import extract_raw_placemarks
from private_fixtures import TESTDATA, read_private, private_corpus




class CameraSiteRelationshipTests(unittest.TestCase):
    def test_extracts_explicit_site_references_without_camera_number(self):
        self.assertEqual(
            named_site_keys_from_camera("Cam 5- for Sites 4.4, 4.5 & 5"),
            ("site:4.4", "site:4.5", "site:5"),
        )
        self.assertEqual(
            named_site_keys_from_camera("Site 4.1 & 4.2- Cam 2"),
            ("site:4.1", "site:4.2"),
        )
        self.assertEqual(
            named_site_keys_from_camera("IC 29 Cam 3"),
            ("ic:29",),
        )

    def test_named_multi_site_camera_uses_nearest_named_site_as_primary(self):
        path = TESTDATA / "survey-d.kml"
        raw = extract_raw_placemarks(read_private(path))
        decisions = classify_raw_placemarks(raw, path.name)
        bundle = build_camera_site_relationship_bundle(raw, decisions)
        by_index = {placemark.placemark_index: placemark for placemark in raw}
        camera = next(
            placemark
            for placemark in raw
            if placemark.placemark_name.startswith("Cam 5-")
        )
        relations = [
            relation
            for relation in bundle.relationships
            if relation.camera_placemark_index == camera.placemark_index
        ]

        self.assertEqual(len(relations), 3)
        primary = next(
            relation for relation in relations if relation.relationship_role == "primary"
        )
        self.assertTrue(
            by_index[primary.site_placemark_index].placemark_name.startswith("Site 4.5-")
        )
        self.assertEqual(
            sum(relation.relationship_role == "fallback" for relation in relations),
            2,
        )

    def test_every_camera_in_current_corpus_has_one_primary_or_review(self):
        camera_count = 0
        for path in private_corpus():
            raw = extract_raw_placemarks(read_private(path))
            decisions = classify_raw_placemarks(raw, path.name)
            observations = build_observation_bundle(raw, decisions)
            bundle = build_camera_site_relationship_bundle(
                raw,
                decisions,
                observations.cameras,
            )
            camera_count += len(observations.cameras)
            primary_counts: dict[int, int] = {}
            for relation in bundle.relationships:
                if relation.relationship_role == "primary":
                    primary_counts[relation.camera_placemark_index] = (
                        primary_counts.get(relation.camera_placemark_index, 0) + 1
                    )
            review_camera_indexes = {
                item.camera_placemark_index for item in bundle.review_items
            }
            for camera in observations.cameras:
                primary_for_camera = primary_counts.get(camera.placemark_index, 0)
                self.assertLessEqual(primary_for_camera, 1)
                self.assertTrue(
                    primary_for_camera == 1
                    or camera.placemark_index in review_camera_indexes
                )

        self.assertGreater(camera_count, 0)

    def test_derived_direction_cameras_receive_primary_sites(self):
        path = TESTDATA / "survey-g-bicycle.kml"
        raw = extract_raw_placemarks(read_private(path))
        decisions = classify_raw_placemarks(raw, path.name)
        observations = build_observation_bundle(raw, decisions)

        bundle = build_camera_site_relationship_bundle(
            raw,
            decisions,
            observations.cameras,
        )

        self.assertEqual(len(observations.cameras), 4)
        self.assertEqual(len(bundle.relationships), 4)
        self.assertTrue(
            all(
                relationship.relationship_role == "primary"
                for relationship in bundle.relationships
            )
        )

    def test_incompatible_task_family_requires_review_without_relationship(self):
        path = TESTDATA / "survey-c.kml"
        raw = extract_raw_placemarks(read_private(path))
        decisions = classify_raw_placemarks(raw, path.name)
        camera_indexes = {
            placemark.placemark_index
            for placemark in raw
            if placemark.placemark_name.startswith("Cam ")
        }
        incompatible_decisions = [
            replace(decision, task_family="od")
            if decision.placemark_index in camera_indexes
            else decision
            for decision in decisions
        ]

        bundle = build_camera_site_relationship_bundle(raw, incompatible_decisions)

        self.assertFalse(bundle.relationships)
        self.assertEqual(len(bundle.review_items), 2)
        self.assertTrue(
            all(item.review_code == "no_compatible_site" for item in bundle.review_items)
        )

    def test_reviewed_camera_only_layer_is_kept_as_supplemental_nearby_evidence(self):
        raw = extract_raw_placemarks(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Folder><name>IC</name>
              <Placemark><name>Site 1</name>
                <Point><coordinates>147.0000000,-42.0000000,0</coordinates></Point>
              </Placemark>
            </Folder>
            <Folder><name>QL</name>
              <Placemark><name>QL Cam 1</name>
                <Point><coordinates>146.9999009,-42.0001787,0</coordinates></Point>
              </Placemark>
            </Folder>
            </Document></kml>"""
        )
        selected = {
            "IC": LayerReviewSelection("ic", ("site",)),
            "QL": LayerReviewSelection("ic", ("camera", "direction")),
        }
        placemarks = filter_selected_placemarks(raw, selected)
        decisions = apply_layer_review_overrides(
            placemarks,
            classify_raw_placemarks(placemarks, "mixed.kml"),
            selected,
        )

        bundle = build_camera_site_relationship_bundle(placemarks, decisions)

        self.assertFalse(bundle.relationships)
        self.assertEqual(len(bundle.review_items), 1)
        self.assertEqual(
            bundle.review_items[0].review_code,
            "layer_without_site_context",
        )

    def test_unnamed_camera_beyond_one_hundred_metres_requires_review(self):
        raw = extract_raw_placemarks(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Folder><name>IC</name>
              <Placemark><name>IC 1- Test Site</name>
                <Point><coordinates>147.0000000,-42.0000000,0</coordinates></Point>
              </Placemark>
              <Placemark><name>Cam 1</name>
                <Point><coordinates>147.0017000,-42.0000000,0</coordinates></Point>
              </Placemark>
            </Folder>
            </Document></kml>"""
        )
        decisions = classify_raw_placemarks(raw, "radius.kml")

        bundle = build_camera_site_relationship_bundle(raw, decisions)

        self.assertFalse(bundle.relationships)
        self.assertEqual(len(bundle.review_items), 1)
        self.assertEqual(
            bundle.review_items[0].review_code,
            "nearest_site_outside_radius",
        )

    def test_od_station_camera_can_use_extended_semantic_distance(self):
        raw = extract_raw_placemarks(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Folder><name>OD Stations</name>
              <Placemark><name>Station 10 - Test Road</name>
                <styleUrl>#icon-1535-9C27B0-nodesc</styleUrl>
                <Point><coordinates>147.0000000,-42.0000000,0</coordinates></Point>
              </Placemark>
              <Placemark><name>Station 11 - Other Road</name>
                <styleUrl>#icon-1535-9C27B0-nodesc</styleUrl>
                <Point><coordinates>147.0045000,-42.0000000,0</coordinates></Point>
              </Placemark>
            </Folder>
            <Folder><name>OD Cameras</name>
              <Placemark><name>NB Cam</name>
                <styleUrl>#icon-1535-9C27B0-nodesc</styleUrl>
                <Point><coordinates>147.0017000,-42.0000000,0</coordinates></Point>
              </Placemark>
            </Folder>
            </Document></kml>"""
        )
        decisions = classify_raw_placemarks(raw, "semantic-radius.kml")

        bundle = build_camera_site_relationship_bundle(raw, decisions)

        self.assertEqual(len(bundle.relationships), 1)
        self.assertEqual(bundle.relationships[0].relationship_role, "primary")
        self.assertIn(
            "extended same-Station evidence radius",
            " ".join(bundle.relationships[0].relationship_reasons),
        )

    def test_od_station_extended_distance_requires_clear_nearest_margin(self):
        raw = extract_raw_placemarks(
            """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
            <Folder><name>OD Stations</name>
              <Placemark><name>Station 10 - Test Road</name>
                <styleUrl>#icon-1535-9C27B0-nodesc</styleUrl>
                <Point><coordinates>147.0000000,-42.0000000,0</coordinates></Point>
              </Placemark>
              <Placemark><name>Station 11 - Other Road</name>
                <styleUrl>#icon-1535-9C27B0-nodesc</styleUrl>
                <Point><coordinates>147.0034000,-42.0000000,0</coordinates></Point>
              </Placemark>
            </Folder>
            <Folder><name>OD Cameras</name>
              <Placemark><name>NB Cam</name>
                <styleUrl>#icon-1535-9C27B0-nodesc</styleUrl>
                <Point><coordinates>147.0017000,-42.0000000,0</coordinates></Point>
              </Placemark>
            </Folder>
            </Document></kml>"""
        )
        decisions = classify_raw_placemarks(raw, "semantic-radius.kml")

        bundle = build_camera_site_relationship_bundle(raw, decisions)

        self.assertFalse(bundle.relationships)
        self.assertEqual(bundle.review_items[0].review_code, "nearest_site_outside_radius")


if __name__ == "__main__":
    unittest.main()
