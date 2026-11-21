"""Offline regression tests for the user-authorized second Daangn cafe.

The existing Daangn cafe remains unchanged. No browser, HTTP, or posts here.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from gh_only.jirum_manual_queue import (
    secondary_discount_eligible, skip_secondary_unqualified, validate_state,
    SECONDARY_STATE_PATH, GithubState,
)
from gh_only.jirum_manual_publisher import (
    RESTORED_CAFE, SECONDARY_CAFE, SECONDARY_BOARD_LABEL,
    target_for_cafe, restored_board_label, restored_live_gate,
)


def deal_article(pct=35, price=6500, reference=10000,
                 basis="lowchart_public_history", similar=False):
    return {
        "source_id": 96771,
        "title": f"[쿠팡🔥핫딜] 실제 검증된 상품 [{pct}%할인]",
        "secondary_discount_evidence": {
            "display_pct": pct,
            "reference_source": basis,
            "comparison_price_krw": reference,
            "sale_price_krw": price,
            "similar_product_only": similar,
        },
    }


def new_state():
    return {
        "schema": "daangn-jirum-manual-fifo-v1",
        "last_scanned_id": 96771,
        "pending": [{
            "id": 96771,
            "source_post_url": "https://t.me/jirum/96771",
            "posted_at_utc": datetime.now(timezone.utc).isoformat(),
            "title": "상품",
            "sale_price_krw": 7000,
            "original_purchase_url": "https://link.coupang.com/a/example",
        }],
        "in_flight": None,
        "published": {
            "94737": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
            "94738": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
            "94743": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
        },
        "skipped_non_coupang": 0,
        "skipped_expired": {},
        "skipped_unverifiable": {},
        "skipped_secondary_discount": {},
    }


class FakeStore:
    def __init__(self, state):
        self.value = deepcopy(state)
        self.writes = []

    def read(self):
        return deepcopy(self.value), "sha"

    def write(self, state, sha, message):
        validate_state(state)
        self.writes.append(message)
        self.value = deepcopy(state)


class SecondaryThresholdTests(unittest.TestCase):
    def test_strictly_above_30_only(self):
        self.assertTrue(secondary_discount_eligible(deal_article(35, 6500)))
        self.assertTrue(secondary_discount_eligible(deal_article(40, 6000)))
        self.assertFalse(secondary_discount_eligible(deal_article(30, 7000)))
        self.assertFalse(secondary_discount_eligible(deal_article(30, 6900)))
        # A 35% title does not justify posting if the source price is 30%.
        self.assertFalse(secondary_discount_eligible(deal_article(35, 7000)))
        # A 35% rounded title still needs the raw rate to be >30%.
        self.assertTrue(secondary_discount_eligible(deal_article(35, 6900)))

    def test_unverified_comparisons_never_qualify(self):
        for kwargs in (
            {"basis": "fallcent_similar_search_current"},
            {"basis": "naver_general_search_first_shopping_card"},
            {"basis": "telegram_unverified_discount"},
            {"basis": None},
            {"similar": True},
            {"price": 10000},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(secondary_discount_eligible(
                    deal_article(**kwargs)))

    def test_title_and_numeric_proof_required(self):
        for patch_article in (
            {"title": "[쿠팡🔥핫딜] 상품 [30%할인]"},
            {"title": "[쿠팡🔥핫딜] 상품 [유사상품가 대비 35%↓]"},
            {"secondary_discount_evidence": {
                "display_pct": True, "reference_source": "lowchart_public_history",
                "comparison_price_krw": 10000, "sale_price_krw": 6500,
            }},
            {"secondary_discount_evidence": None},
        ):
            with self.subTest(patch_article=patch_article):
                self.assertFalse(secondary_discount_eligible(
                    {**deal_article(), **patch_article}))

    def test_below_threshold_is_audited_without_claim_or_post(self):
        state = new_state()
        store = FakeStore(state)
        item = deal_article(30, 7000)
        skip_secondary_unqualified(store, 96771, item)
        self.assertEqual(store.value["pending"], [])
        self.assertIsNone(store.value["in_flight"])
        self.assertNotIn("96771", store.value["published"])
        self.assertEqual(
            store.value["skipped_secondary_discount"]["96771"]["status"],
            "NOT_OVER_30_VERIFIED",
        )
        self.assertEqual(len(store.writes), 1)
        with self.assertRaisesRegex(RuntimeError, "QUEUE_CHANGED"):
            skip_secondary_unqualified(store, 96771, item)

    def test_secondary_audit_cannot_overlap_with_pending_or_published(self):
        state = new_state()
        state["skipped_secondary_discount"]["96771"] = {
            "status": "NOT_OVER_30_VERIFIED"
        }
        with self.assertRaisesRegex(ValueError, "INVALID_SECONDARY_DISCOUNT_AUDIT"):
            validate_state(state)
        state["pending"].clear()
        state["published"]["96771"] = {"status": "PUBLISHED_VERIFIED"}
        with self.assertRaisesRegex(ValueError, "INVALID_SECONDARY_DISCOUNT_AUDIT"):
            validate_state(state)


class SecondaryPermissionsTests(unittest.TestCase):
    def test_existing_cafe_unchanged(self):
        with patch.dict(os.environ, {"DAANGN_SECONDARY_CAFE_MODE": "false"}):
            self.assertEqual(target_for_cafe(RESTORED_CAFE)[1],
                             "/kupang-teuggaye/posts/")
            with self.assertRaisesRegex(RuntimeError, "TARGET_NOT_APPROVED"):
                target_for_cafe(SECONDARY_CAFE)

    def test_secondary_only_allows_approved_cafe(self):
        with patch.dict(os.environ, {"DAANGN_SECONDARY_CAFE_MODE": "true"}):
            self.assertEqual(target_for_cafe(SECONDARY_CAFE),
                             (SECONDARY_CAFE + "/posts/new",
                              "/geojijeol-yagje/posts/"))
            with self.assertRaisesRegex(RuntimeError, "TARGET_NOT_APPROVED"):
                target_for_cafe(RESTORED_CAFE)
            with self.assertRaisesRegex(RuntimeError, "TARGET_NOT_APPROVED"):
                target_for_cafe("https://cafe.daangn.com/another-cafe")

    def test_secondary_admin_permission_mandatory(self):
        valid = {
            "DAANGN_CAFE_RESTORED": "true",
            "DAANGN_CAFE_PUBLISH_ENABLED": "true",
            "DAANGN_CAFE_PARTNERS_PERMISSION_CONFIRMED": "true",
            "DAANGN_PUBLISH_PAUSED": "false",
            "DAANGN_OFFICIAL_API_LINK_ENABLED": "true",
            "COUPANG_PARTNER_SUBID": "",
            "DAANGN_SECONDARY_CAFE_MODE": "true",
            "DAANGN_SECONDARY_CAFE_ADMIN_PERMISSION_CONFIRMED": "true",
            "DAANGN_CAFE_TARGET_URL": SECONDARY_CAFE,
            "DAANGN_CAFE_BOARD_LABEL": SECONDARY_BOARD_LABEL,
        }
        with patch.dict(os.environ, valid):
            self.assertTrue(restored_live_gate())
            self.assertEqual(restored_board_label(), SECONDARY_BOARD_LABEL)
        for missing in (
            "DAANGN_SECONDARY_CAFE_ADMIN_PERMISSION_CONFIRMED",
            "DAANGN_CAFE_TARGET_URL", "DAANGN_CAFE_BOARD_LABEL",
        ):
            with self.subTest(missing=missing):
                with patch.dict(os.environ, {**valid, missing: "false"}):
                    self.assertFalse(restored_live_gate())

    def test_state_path_is_isolated(self):
        with patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "mw5114-tech/hotdeal-bot",
            "GH_STATE_TOKEN": "testing-only",
            "DAANGN_SECONDARY_CAFE_MODE": "true",
        }):
            self.assertTrue(GithubState().url.endswith(SECONDARY_STATE_PATH))
        with patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "mw5114-tech/hotdeal-bot",
            "GH_STATE_TOKEN": "testing-only",
            "DAANGN_SECONDARY_CAFE_MODE": "false",
        }):
            self.assertTrue(GithubState().url.endswith(
                "state/daangn_jirum_manual_fifo.json"
            ))


if __name__ == "__main__":
    unittest.main()
