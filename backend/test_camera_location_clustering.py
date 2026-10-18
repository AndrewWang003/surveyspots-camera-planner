import unittest

from app.camera_location_clustering import (
    CameraLocationEvidence,
    build_camera_location_clusters,
)


METRES_PER_LONGITUDE_DEGREE_AT_EQUATOR = 111_195.0


def position_at_metres(east_m: float):
    return (east_m / METRES_PER_LONGITUDE_DEGREE_AT_EQUATOR, 0.0, 0.0)


class CameraLocationClusteringTests(unittest.TestCase):
    def test_complete_link_does_not_chain_distant_endpoints(self):
        evidence = [
            CameraLocationEvidence("a", position_at_metres(0.0)),
            CameraLocationEvidence("b", position_at_metres(1.0)),
            CameraLocationEvidence("c", position_at_metres(2.0)),
        ]

        clusters = build_camera_location_clusters(evidence, 1.5, "test-v1")

        self.assertEqual(sorted(len(cluster.members) for cluster in clusters), [1, 2])
        self.assertTrue(all(cluster.diameter_m <= 1.5 for cluster in clusters))

    def test_output_is_independent_of_input_order(self):
        evidence = [
            CameraLocationEvidence("a", position_at_metres(0.0)),
            CameraLocationEvidence("b", position_at_metres(0.5)),
            CameraLocationEvidence("c", position_at_metres(10.0)),
        ]

        forward = build_camera_location_clusters(evidence, 1.5, "test-v1")
        reverse = build_camera_location_clusters(
            list(reversed(evidence)),
            1.5,
            "test-v1",
        )

        self.assertEqual(forward, reverse)

    def test_representative_is_an_actual_historical_coordinate(self):
        evidence = [
            CameraLocationEvidence("a", position_at_metres(0.0)),
            CameraLocationEvidence("b", position_at_metres(0.5)),
            CameraLocationEvidence("c", position_at_metres(1.0)),
        ]

        cluster = build_camera_location_clusters(evidence, 1.5, "test-v1")[0]

        self.assertEqual(cluster.representative_evidence_id, "b")
        self.assertEqual(cluster.representative_position, evidence[1].position)


if __name__ == "__main__":
    unittest.main()
