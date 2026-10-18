import unittest

from app.canonical_site import CanonicalSiteEvidence, build_canonical_sites


METRES_PER_LONGITUDE_DEGREE_AT_EQUATOR = 111_195.0


def site(site_id, upload_id, east_m, task_family="ic"):
    return CanonicalSiteEvidence(
        site_observation_id=site_id,
        raw_upload_id=upload_id,
        position=(east_m / METRES_PER_LONGITUDE_DEGREE_AT_EQUATOR, 0.0),
        task_family=task_family,
    )


class CanonicalSiteTests(unittest.TestCase):
    def test_does_not_chain_distant_site_observations(self):
        candidates = build_canonical_sites(
            [
                site("a", "upload-a", 0.0),
                site("b", "upload-b", 20.0),
                site("c", "upload-c", 40.0),
            ],
            25.0,
            "test-v1",
        )

        self.assertEqual(sorted(len(item.members) for item in candidates), [1, 2])
        self.assertTrue(all(item.diameter_m <= 25.0 for item in candidates))

    def test_nearby_sites_in_same_upload_remain_independent(self):
        candidates = build_canonical_sites(
            [site("a", "upload-a", 0.0), site("b", "upload-a", 1.0)],
            25.0,
            "test-v1",
        )

        self.assertEqual(len(candidates), 2)

    def test_nearby_compatible_sites_from_different_uploads_merge(self):
        candidates = build_canonical_sites(
            [site("a", "upload-a", 0.0), site("b", "upload-b", 5.0)],
            25.0,
            "test-v1",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(candidates[0].members), 2)


if __name__ == "__main__":
    unittest.main()
