"""Offline regression for Daangn's duplicate '글쓰기' buttons.

No network calls and no click happens in these tests.
"""
import unittest

from gh_only.jirum_manual_publisher import (
    choose_editor_submit_index, fit_title_to_limit, leading_image_ui_prefix,
)


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


class DaangnTitleLengthTests(unittest.TestCase):
    def test_long_title_retains_discount_suffix_with_fifty_char_limit(self):
        title = "[오늘의 핫딜] 바닐라코 클린 잇 제로 포어 클래리파잉 클렌징 밤, 180ml, 1개 [25%할인]"
        self.assertGreater(len(title), 50)
        fitted = fit_title_to_limit(title, 50)
        self.assertLessEqual(len(fitted), 50)
        self.assertTrue(fitted.startswith("[오늘의 핫딜] 바닐라코"))
        self.assertTrue(fitted.endswith("[25%할인]"))

    def test_short_title_stays_unchanged(self):
        title = "[오늘의 핫딜] 샐러드 컵 [15%할인]"
        self.assertEqual(fit_title_to_limit(title, 50), title)

    def test_without_discount_truncates_safely(self):
        title = "[오늘의 핫딜] " + "상품" * 35
        self.assertLessEqual(len(fit_title_to_limit(title, 45)), 45)

    def test_implausible_title_length_rejected(self):
        with self.assertRaises(ValueError):
            fit_title_to_limit("긴 제목", 4)


class EditorPhotoPrefixTests(unittest.TestCase):
    def test_short_image_ui_prefix_before_exact_body(self):
        body = "상품명 테스트\\n🛒 상품 링크 : https://link.coupang.com/re/AFFSDP?key=1\\n이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
        observed = "이미지 업로드 상태표시와 관리메뉴\\n" + body
        self.assertEqual(
            leading_image_ui_prefix(observed, body),
            "이미지업로드상태표시와관리메뉴",
        )

    def test_changed_body_is_rejected(self):
        body = "상품 링크 https://link.coupang.com/re/AFFSDP?key=1"
        self.assertIsNone(leading_image_ui_prefix("사진 " + body.replace("key=1", "key=2"), body))

    def test_extra_text_after_footer_is_rejected(self):
        body = "상품 링크 https://link.coupang.com/re/AFFSDP?key=1"
        self.assertIsNone(leading_image_ui_prefix("이미지 " + body + " 추가 문구", body))

    def test_excessive_prefix_is_rejected(self):
        body = "본문 그대로"
        self.assertIsNone(leading_image_ui_prefix("허용안됨" * 40 + body, body))

    def test_normalized_whitespace_is_accepted(self):
        self.assertEqual(leading_image_ui_prefix("A  B\\n C", "A B C"), "")


if __name__ == "__main__":
    unittest.main()
