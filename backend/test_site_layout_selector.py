from datetime import datetime, timezone
import unittest

from app.site_layout_selector import (
    project_key_from_source,
    select_site_layout_preference,
)


def row(
    cluster_id,
    upload_id,
    project_file,
    observed_at,
    site_observation_id="site-1",
):
    return {
        "cluster_id": cluster_id,
        "selection_tier": "primary",
        "relationship_id": f"relationship-{upload_id}-{cluster_id}",
        "layout_raw_upload_id": upload_id,
        "layout_site_observation_id": site_observation_id,
        "layout_source_file": project_file,
        "layout_uploaded_at": observed_at,
    }


class SiteLayoutSelectorTests(unittest.TestCase):
    def test_most_frequent_complete_layout_becomes_base(self):
        first = datetime(2025, 1, 1, tzinfo=timezone.utc)
        second = datetime(2025, 2, 1, tzinfo=timezone.utc)
        third = datetime(2025, 3, 1, tzinfo=timezone.utc)
        rows = [
            row("camera-a", "upload-1", "JOB100 Project.kml", first),
            row("camera-b", "upload-1", "JOB100 Project.kml", first),
            row("camera-a", "upload-2", "JOB200 Project.kml", second),
            row("camera-b", "upload-2", "JOB200 Project.kml", second),
            row("camera-a", "upload-3", "JOB300 Project.kml", third),
            row("camera-c", "upload-3", "JOB300 Project.kml", third),
        ]

        preference = select_site_layout_preference(rows)

        self.assertEqual(
            preference.base_cluster_ids,
            frozenset({"camera-a", "camera-b"}),
        )
        self.assertEqual(preference.base_layout_frequency, 2)
        self.assertEqual(preference.project_vote_count, 3)
        self.assertEqual(preference.distinct_layout_count, 2)
        self.assertEqual(preference.cluster_frequencies["camera-a"], 3)
        self.assertEqual(
            preference.role_for_cluster("camera-c", "primary"),
            "supplemental_historical",
        )

    def test_latest_revision_replaces_same_project_vote_only(self):
        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        new = datetime(2025, 2, 1, tzinfo=timezone.utc)
        rows = [
            row("camera-old", "upload-old", "JOB100 Project.kml", old),
            row("camera-new", "upload-new", "JOB100 Revision.kml", new),
        ]

        preference = select_site_layout_preference(rows)

        self.assertEqual(preference.project_vote_count, 1)
        self.assertEqual(preference.base_cluster_ids, frozenset({"camera-new"}))

    def test_no_camera_rows_create_no_zero_camera_vote(self):
        preference = select_site_layout_preference([])

        self.assertFalse(preference.base_cluster_ids)
        self.assertEqual(preference.project_vote_count, 0)

    def test_project_id_uses_job_number(self):
        self.assertEqual(
            project_key_from_source("JOB00123 Updated.kml", "upload-1"),
            "project:123",
        )


if __name__ == "__main__":
    unittest.main()
