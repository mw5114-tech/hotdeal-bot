"""Offline regression for Daangn's duplicate '글쓰기' buttons.

No network calls and no click happens in these tests.
"""
import unittest

from gh_only.jirum_manual_publisher import choose_editor_submit_index


class SubmitAnchorTests(unittest.TestCase):
    def test_header_write_button_is_never_selected(self):
        buttons = [
            {"index": 0, "visible": True, "below_editor": False, "enabled": True},
            {"index": 1, "visible": True, "below_editor": True, "enabled": False},
        ]
        self.assertEqual(choose_editor_submit_index(buttons), 1)

    def test_two_below_editor_are_ambiguous(self):
        buttons = [
            {"index": 0, "visible": True, "below_editor": True},
            {"index": 1, "visible": True, "below_editor": True},
        ]
        self.assertIsNone(choose_editor_submit_index(buttons))

    def test_hidden_and_top_buttons_are_not_selected(self):
        buttons = [
            {"index": 0, "visible": True, "below_editor": False},
            {"index": 1, "visible": False, "below_editor": True},
        ]
        self.assertIsNone(choose_editor_submit_index(buttons))

    def test_one_valid_editor_submit(self):
        buttons = [{"index": 3, "visible": True, "below_editor": True}]
        self.assertEqual(choose_editor_submit_index(buttons), 3)


if __name__ == "__main__":
    unittest.main()
