import unittest

from app.rbac import allowed_levels, can_read, can_relabel, initial_level

LEVELS = ["employee", "super"]


class RBACTests(unittest.TestCase):
    def test_super_reads_everything(self):
        self.assertEqual(allowed_levels("super", LEVELS), ["employee", "super"])

    def test_employee_cannot_read_super(self):
        self.assertEqual(allowed_levels("employee", LEVELS), ["employee"])
        self.assertFalse(can_read("employee", "super", LEVELS))

    def test_unknown_user_level_gets_nothing(self):
        self.assertEqual(allowed_levels("admin?", LEVELS), [])
        self.assertFalse(can_read("", "employee", LEVELS))

    def test_unknown_item_level_is_never_readable(self):
        self.assertFalse(can_read("super", "mystery", LEVELS))

    def test_three_levels(self):
        lv = ["a", "b", "c"]
        self.assertEqual(allowed_levels("b", lv), ["a", "b"])

    def test_new_uploads_start_at_highest_by_default(self):
        self.assertEqual(initial_level("highest", LEVELS), "super")

    def test_new_uploads_can_start_at_a_named_level(self):
        self.assertEqual(initial_level("employee", LEVELS), "employee")

    def test_unknown_default_level_rejected(self):
        with self.assertRaises(ValueError):
            initial_level("nope", LEVELS)

    def test_only_top_level_can_relabel(self):
        self.assertTrue(can_relabel("super", LEVELS))
        self.assertFalse(can_relabel("employee", LEVELS))
        self.assertFalse(can_relabel("", LEVELS))


if __name__ == "__main__":
    unittest.main()
