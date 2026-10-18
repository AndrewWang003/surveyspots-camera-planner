import unittest

from app.camera_quantity import infer_camera_quantity


class CameraQuantityTests(unittest.TestCase):
    def test_counts_numbered_camera_references(self):
        self.assertEqual(infer_camera_quantity("IC 8- Cam 4 & 5"), 2)
        self.assertEqual(infer_camera_quantity("EB Cam 1 & 2- 1 Hik & 1 IPOD"), 2)
        self.assertEqual(infer_camera_quantity("QL- Cam 2 & Cam 3"), 2)

    def test_counts_equipment_quantities(self):
        self.assertEqual(
            infer_camera_quantity("Station 12a- EB Cam", "1 Hik + 1 IPOD"),
            2,
        )
        self.assertEqual(
            infer_camera_quantity("Station 12a- EB Cam - 1 Hik + 1 IPOD"),
            2,
        )
        self.assertEqual(infer_camera_quantity("Install location", "2 cameras"), 2)

    def test_uses_direction_count_as_supporting_evidence(self):
        self.assertEqual(infer_camera_quantity("IC 8- Camera Location", "", 2), 2)

    def test_single_camera_reference_stays_one(self):
        self.assertEqual(infer_camera_quantity("Camera 2 location"), 1)
        self.assertEqual(infer_camera_quantity("Cam 4"), 1)
        self.assertEqual(infer_camera_quantity("IC 29 cam 1"), 1)
        self.assertEqual(infer_camera_quantity("Station 12a EB Cam"), 1)


if __name__ == "__main__":
    unittest.main()
