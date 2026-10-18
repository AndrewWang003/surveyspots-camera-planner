import unittest

from app.task_family import infer_task_family, task_families_compatible


class TaskFamilyTests(unittest.TestCase):
    def test_infers_task_family_from_user_visible_text(self):
        self.assertEqual(infer_task_family("OD Sites"), "od")
        self.assertEqual(infer_task_family("IPOD Cameras"), "ipod")
        self.assertEqual(infer_task_family("HIK Locations"), "hik")
        self.assertEqual(infer_task_family("ATC Tube Lines"), "atc")
        self.assertEqual(infer_task_family("IC Sites"), "ic")

    def test_od_and_ipod_are_compatible(self):
        self.assertTrue(task_families_compatible("od", "ipod"))
        self.assertTrue(task_families_compatible("ipod", "od"))

    def test_ic_hik_and_atc_do_not_cross_match(self):
        self.assertFalse(task_families_compatible("ic", "od"))
        self.assertFalse(task_families_compatible("ic", "hik"))
        self.assertFalse(task_families_compatible("hik", "od"))
        self.assertFalse(task_families_compatible("atc", "od"))


if __name__ == "__main__":
    unittest.main()
