import unittest

from app.camera_count_selector import (
    SiteCameraCountEvidence,
    select_site_camera_count,
)


def evidence(
    evidence_id: str,
    upload_id: str,
    camera_count: int,
    observed_order: float,
    *,
    required_count: int | None = None,
    position=(145.0, -42.0),
    task_family="ic",
    primary_camera_count: int | None = None,
):
    return SiteCameraCountEvidence(
        site_observation_id=evidence_id,
        raw_upload_id=upload_id,
        position=position,
        task_family=task_family,
        required_camera_count=required_count,
        camera_count_source=("explicit_description" if required_count else ""),
        primary_camera_location_ids=tuple(
            f"{evidence_id}-camera-{index}" for index in range(camera_count)
        ),
        observed_order=observed_order,
        primary_camera_count=primary_camera_count,
    )


class CameraCountSelectorTests(unittest.TestCase):
    def test_explicit_count_overrides_historical_mode(self):
        target = evidence("target", "upload-a", 1, 3.0, required_count=2)
        history = [
            target,
            evidence("older-1", "upload-b", 3, 1.0),
            evidence("older-2", "upload-c", 3, 2.0),
        ]

        decision = select_site_camera_count(target, history, 25.0)

        self.assertEqual(decision.required_camera_count, 2)
        self.assertEqual(decision.camera_count_source, "explicit_description")

    def test_primary_camera_count_conflict_uses_larger_count(self):
        target = evidence(
            "target",
            "upload-a",
            3,
            3.0,
            required_count=2,
            primary_camera_count=3,
        )

        decision = select_site_camera_count(target, [target], 25.0)

        self.assertEqual(decision.required_camera_count, 3)
        self.assertEqual(
            decision.camera_count_source,
            "explicit_description_conflict_primary",
        )
        self.assertFalse(decision.requires_review)

    def test_uses_mode_of_distinct_primary_camera_locations(self):
        target = evidence("target", "upload-a", 2, 4.0)
        history = [
            target,
            evidence("older-1", "upload-b", 2, 1.0),
            evidence("older-2", "upload-c", 3, 2.0),
        ]

        decision = select_site_camera_count(target, history, 25.0)

        self.assertEqual(decision.required_camera_count, 2)
        self.assertEqual(decision.historical_count_votes, ((2, 2), (3, 1)))
        self.assertEqual(decision.camera_count_source, "historical_mode")

    def test_mode_tie_uses_larger_count(self):
        target = evidence("target", "upload-a", 2, 1.0)
        newer = evidence("newer", "upload-b", 3, 0.5)

        decision = select_site_camera_count(target, [target, newer], 25.0)

        self.assertEqual(decision.required_camera_count, 3)

    def test_uses_inferred_primary_camera_quantity_when_available(self):
        target = evidence("target", "upload-a", 1, 3.0, primary_camera_count=2)
        older = evidence("older", "upload-b", 1, 2.0, primary_camera_count=2)

        decision = select_site_camera_count(target, [target, older], 25.0)

        self.assertEqual(decision.required_camera_count, 2)
        self.assertEqual(decision.historical_count_votes, ((2, 2),))

    def test_nearby_sites_in_same_upload_do_not_vote_for_each_other(self):
        target = evidence("target", "upload-a", 0, 2.0)
        neighbour = evidence("neighbour", "upload-a", 3, 2.0)

        decision = select_site_camera_count(target, [target, neighbour], 25.0)

        self.assertIsNone(decision.required_camera_count)
        self.assertTrue(decision.requires_review)


if __name__ == "__main__":
    unittest.main()
