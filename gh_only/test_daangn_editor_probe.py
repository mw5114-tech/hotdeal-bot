"""Offline Daangn browser article/session contracts. Never visit a website."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gh_only.daangn_editor_probe import (
    DISCLOSURE, choose_visible_unique, parse_daangn_storage_state,
    read_one_draft, validate_cafe_url,
)

TEST_URL = ("https://link.coupang.com/re/AFFSDP?"
            "lptag=AF9668915&pageKey=9217210892&itemId=27232840915")


class Locator:
    def __init__(self, visible):
        self.visible = visible

    def count(self):
        return len(self.visible)

    def nth(self, index):
        return Item(self.visible[index])


class Item:
    def __init__(self, visible):
        self.visible = visible

    def is_visible(self, timeout=None):
        return self.visible


class DaangnEditorOfflineTests(unittest.TestCase):
    def test_exact_cafe_url_required(self):
        self.assertEqual(
            validate_cafe_url("https://cafe.daangn.com/own-cafe/posts"),
            "https://cafe.daangn.com/own-cafe",
        )
        for s in ("", "https://www.daangn.com/own-cafe",
                  "http://cafe.daangn.com/own-cafe",
                  "https://cafe.daangn.com.evil.test/own-cafe",
                  "https://cafe.daangn.com/own-cafe/posts/new",
                  "https://cafe.daangn.com/own-cafe?access=secrets",
                  "https://cafe.daangn.com/"):
            with self.subTest(s=s), self.assertRaises(ValueError):
                validate_cafe_url(s)

    def test_auth_filters_unrelated_third_party_cookies(self):
        data = {"cookies": [
            {"name": "daangn_session", "value": "not-real", "domain": ".daangn.com", "path": "/"},
            {"name": "bad", "value": "fake", "domain": ".daangn.com.evil.test", "path": "/"},
        ], "origins": [
            {"origin": "https://cafe.daangn.com", "localStorage": []},
            {"origin": "https://evil.test", "localStorage": []},
        ]}
        parsed = parse_daangn_storage_state(json.dumps(data))
        self.assertEqual(len(parsed["cookies"]), 1)
        self.assertEqual(len(parsed["origins"]), 1)
        self.assertEqual(parsed["cookies"][0]["name"], "daangn_session")

    def test_invalid_state_doesnt_fall_through(self):
        for s in ("", "{}", "garbage", "[]", json.dumps({"cookies": [], "origins": []}),
                  json.dumps({"cookies": [{"domain": "evil.com"}], "origins": []})):
            with self.subTest(s=s), self.assertRaises(ValueError):
                parse_daangn_storage_state(s)

    def _make_draft(self, folder):
        body = (
            "📦 상품명 : 휘카레 셰프 오리지널 소스 순한맛\n\n"
            "💸 쿠팡 평소 판매가 : 약 34,575원\n\n"
            "🔥 핫딜 특가 : 12,050원\n\n"
            "🛒 상품 링크 : " + TEST_URL
        )
        report = {"schema": "github-jirum-default-price-photo-affsdp-draft-v2", "drafts": [{
            "source_message_id": 94717,
            "source_post_url": "https://t.me/jirum/94717",
            "caption": {"caption": body},
            "composed_affiliate_url": TEST_URL,
            "affsdp": {
                "status": "UNISSUED_AFFSDP_CANDIDATE",
                "official_link_issued": False,
                "commission_attribution_verified": False,
            },
            "photo": {"status": "IMAGE_SAVED", "saved_name": "jirum_94717_kakao_og.jpg"},
        }]}
        (folder / "drafts.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        (folder / "jirum_94717_kakao_og.jpg").write_bytes(b"\xff\xd8\xff" + b"stub")
        return report

    def test_draft_prepends_title_keeps_disclosure_and_exact_photo(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self._make_draft(folder)
            result = read_one_draft(folder)
        self.assertIn("[오늘의 핫딜]", result["title"])
        self.assertIn("12,050원", result["body"])
        self.assertIn("AF9668915", result["body"])
        self.assertIn(DISCLOSURE, result["body"])
        self.assertEqual(result["body"].splitlines()[-1], DISCLOSURE)
        self.assertTrue(result["body"].endswith("카페 가입 알림 설정하고 꿀템 챙겨가세요." + "\n\n\n\n" + DISCLOSURE))
        self.assertEqual(result["candidate_url"], TEST_URL)
        self.assertLess(result["body"].index(TEST_URL), result["body"].index(DISCLOSURE))
        self.assertIn("※ 실시간 핫딜 특성상", result["body"])
        self.assertIn("🔥 핫딜 정보를 확인해 소개합니다.", result["body"])
        self.assertNotIn("검증 마친 찐 역대가", result["body"])
        self.assertEqual(result["image"].name, "jirum_94717_kakao_og.jpg")
        self.assertTrue(result["unissued_affsdp_candidate"])

    def test_verified_historical_low_uses_requested_promo_and_discount_title(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            report = self._make_draft(folder)
            report["drafts"][0]["observed_low_verified_for_deal"] = True
            report["drafts"][0]["caption"]["approx_discount_vs_historical_mean_pct"] = 65
            (folder / "drafts.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            result = read_one_draft(folder)
        self.assertIn("[65%할인]", result["title"])
        self.assertIn("검증 마친 찐 역대가 딜만 가져옵니다.", result["body"])
        self.assertTrue(result["body"].endswith(DISCLOSURE))

    def test_does_not_accept_original_source_affiliate_instead_of_own_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            report = self._make_draft(folder)
            report["drafts"][0]["composed_affiliate_url"] = "https://link.coupang.com/a/iiPBYpVHae"
            (folder / "drafts.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_one_draft(folder)

    def test_missing_photo_is_failure_not_guesswork(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self._make_draft(folder)
            (folder / "jirum_94717_kakao_og.jpg").unlink()
            with self.assertRaisesRegex(ValueError, "DRAFT_IMAGE_NOT_PRESENT"):
                read_one_draft(folder)

    def test_duplicate_selector_matches_dont_create_ambiguity(self):
        loc = choose_visible_unique([Locator([True, True]), Locator([False, True])])
        self.assertIsNotNone(loc)
        self.assertTrue(loc.visible)
        self.assertIsNone(choose_visible_unique([Locator([False]), Locator([])]))


if __name__ == "__main__":
    unittest.main()
