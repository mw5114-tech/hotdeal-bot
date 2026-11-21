"""Offline, no-browser tests for Naver's safe title-only recovery.

No logins, web calls, or publishing; a deterministic fake SmartEditor models
the single-character title loss observed on Telegram source 96694.
"""
import unittest

from gh_only.naver_blog_deal_publisher import (
    NaverPreSubmitTitleError, _exact_editor_title, apply_verified_title,
)


TITLE = "[쿠팡🔥핫딜] 데니그리스 유기농 사과식초 500ml [40%할인]"


class FakeEditor:
    def __init__(self, mode="partial", safe_selection=True):
        self.mode = mode
        self.safe_selection = safe_selection
        self.title = ""
        self.body = ""
        self.phase = "initial"
        self.actions = []
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector):
        return FakeLocator(self, selector)

    def evaluate(self, _script):
        self.actions.append("evaluate_title_selection")
        return {"selected": self.title, "inside_title": self.safe_selection}

    def wait_for_timeout(self, ms):
        self.actions.append(("wait", ms))


class FakeLocator:
    def __init__(self, doc, selector):
        self.doc = doc
        self.selector = selector

    def count(self):
        if self.selector == ".se-section-documentTitle .se-module.se-is-empty":
            return 1 if not self.doc.title else 0
        if self.selector == ".se-section-text .se-module.se-is-empty":
            return 1 if not self.doc.body else 0
        return 1

    def inner_text(self):
        if self.selector == ".se-section-documentTitle":
            return self.doc.title
        if self.selector == ".se-section-text":
            return self.doc.body
        raise AssertionError("Unexpected read selector " + self.selector)

    def click(self, timeout=None):
        self.doc.actions.append(("title_click", timeout))


class FakeKeyboard:
    def __init__(self, doc):
        self.doc = doc

    def insert_text(self, text):
        self.doc.actions.append(("insert", text))
        if self.doc.mode != "empty":
            self.doc.title += text

    def type(self, text, delay=None):
        self.doc.actions.append(("type", len(text)))
        if self.doc.phase == "initial" and self.doc.mode in ("partial", "empty"):
            return
        if self.doc.mode == "cannot_recover":
            return
        self.doc.title += text

    def press(self, key):
        self.doc.actions.append(("press", key))
        if key == "ControlOrMeta+A":
            return
        if key == "Backspace":
            if not self.doc.safe_selection:
                raise AssertionError("Unsafe selection must not be deleted")
            self.doc.title = ""
            self.doc.phase = "recovery"
            return
        raise AssertionError("Unexpected key " + key)


class TitleRecoveryTests(unittest.TestCase):
    def test_partial_first_character_is_replaced_once(self):
        doc = FakeEditor(mode="partial")
        apply_verified_title(doc, doc, doc.locator("title-target"), TITLE)
        self.assertTrue(_exact_editor_title(doc, TITLE))
        self.assertEqual(doc.body, "")
        self.assertEqual(
            [x for x in doc.actions if x == ("press", "Backspace")],
            [("press", "Backspace")],
        )
        self.assertEqual(
            [x for x in doc.actions if x == ("press", "ControlOrMeta+A")],
            [("press", "ControlOrMeta+A")],
        )

    def test_fully_accepted_title_never_triggers_selection(self):
        doc = FakeEditor(mode="success")
        apply_verified_title(doc, doc, doc.locator("title-target"), TITLE)
        self.assertTrue(_exact_editor_title(doc, TITLE))
        self.assertFalse(any(x == ("press", "ControlOrMeta+A") for x in doc.actions))

    def test_fully_missing_title_can_retry_once_without_selecting_body(self):
        doc = FakeEditor(mode="empty")
        apply_verified_title(doc, doc, doc.locator("title-target"), TITLE)
        self.assertTrue(_exact_editor_title(doc, TITLE))
        self.assertFalse(any(isinstance(x, tuple) and x[0] == "press"
                             for x in doc.actions))
        self.assertEqual(doc.body, "")

    def test_selection_outside_title_fails_without_deleting(self):
        doc = FakeEditor(mode="partial", safe_selection=False)
        with self.assertRaisesRegex(NaverPreSubmitTitleError, "SELECTION_UNVERIFIED"):
            apply_verified_title(doc, doc, doc.locator("title-target"), TITLE)
        self.assertEqual(doc.title, TITLE[0])
        self.assertFalse(any(x == ("press", "Backspace") for x in doc.actions))

    def test_rewrite_failure_fails_closed(self):
        doc = FakeEditor(mode="cannot_recover")
        with self.assertRaisesRegex(NaverPreSubmitTitleError, "REWRITE_NOT_VERIFIED"):
            apply_verified_title(doc, doc, doc.locator("title-target"), TITLE)
        self.assertNotEqual(doc.title, TITLE)

    def test_existing_body_content_blocks_initial_title_action(self):
        doc = FakeEditor(mode="partial")
        doc.body = "Existing nonempty draft"
        with self.assertRaisesRegex(NaverPreSubmitTitleError, "EDITOR_NOT_EMPTY"):
            apply_verified_title(doc, doc, doc.locator("title-target"), TITLE)
        self.assertEqual(doc.actions, [])

    def test_final_title_requires_exact_value_not_substring(self):
        doc = FakeEditor(mode="partial")
        doc.title = TITLE + " EXTRA"
        self.assertFalse(_exact_editor_title(doc, TITLE))
        doc.title = TITLE
        self.assertTrue(_exact_editor_title(doc, TITLE))


if __name__ == "__main__":
    unittest.main()
