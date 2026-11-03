"""Offline FIFO regression tests. Never contact Telegram, GitHub or Daangn."""
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gh_only.jirum_manual_queue import (
    START_AFTER, SCHEMA, check_run_trigger, collect_since, finish, ingest,
    publish_pending, scan, validate_state, kakao_secret_fingerprint,
    freshness_status, expire_pending, scan_with_source_wait,
)


def baseline():
    return {
        "schema": SCHEMA, "last_scanned_id": START_AFTER,
        "pending": [], "in_flight": None,
        "published": {
            "94737": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
            "94738": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
            "94743": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
        },
        "skipped_non_coupang": 0,
    }


def deal(mid, kind="coupang_shortlink", price=12300):
    url = (f"https://link.coupang.com/a/example{mid}"
           if kind == "coupang_shortlink" else "https://example.com/item")
    return {
        "source_message_id": mid,
        "source_post_url": f"https://t.me/jirum/{mid}",
        "original_purchase_url": url,
        "url_kind": kind,
        "source_text": f"상품번호 {mid}\n특가 {price}원",
        "title": f"상품번호 {mid}",
        "sale_price_krw": price,
    }


class FakeStore:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.writes = []

    def read(self):
        return copy.deepcopy(self.value), "sha-test"

    def write(self, state, sha, message):
        if sha != "sha-test":
            raise ValueError("sha mismatch")
        validate_state(state)
        self.value = copy.deepcopy(state)
        self.writes.append(message)


class ManualQueueTests(unittest.TestCase):
    def test_event_waits_for_public_source_then_uses_existing_fifo(self):
        store = FakeStore(baseline())
        visible = {'status': 'READY_ONE', 'has_deal': True, 'last_scanned_id': 94904}
        scanner = unittest.mock.Mock(side_effect=[
            {'status': 'NO_NEW_COUPANG_DEAL', 'has_deal': False, 'last_scanned_id': 94903},
            visible,
        ])
        sleeper = unittest.mock.Mock()
        self.assertEqual(scan_with_source_wait(store, 94904, scanner=scanner, sleeper=sleeper), visible)
        self.assertEqual(scanner.call_count, 2)
        sleeper.assert_called_once_with(20)
        self.assertEqual(store.writes, [])

    def test_public_source_wait_is_bounded_and_never_dispatches_or_posts(self):
        result = {'status': 'NO_NEW_COUPANG_DEAL', 'has_deal': False, 'last_scanned_id': 94903}
        scanner = unittest.mock.Mock(return_value=result)
        sleeper = unittest.mock.Mock()
        self.assertEqual(scan_with_source_wait(None, 94904, scanner=scanner, sleeper=sleeper), result)
        self.assertEqual(scanner.call_count, 4)
        self.assertEqual(sleeper.call_count, 3)

    def test_backup_or_review_locked_fifo_never_waits_for_source(self):
        for expected, result in (
            (0, {'status': 'NO_NEW_COUPANG_DEAL'}),
            (94904, {'status': 'REVIEW_REQUIRED'}),
            (94904, {'status': 'NO_NEW_COUPANG_DEAL', 'last_scanned_id': 94904}),
        ):
            scanner = unittest.mock.Mock(return_value=result)
            sleeper = unittest.mock.Mock()
            self.assertEqual(scan_with_source_wait(None, expected, scanner=scanner, sleeper=sleeper), result)
            scanner.assert_called_once_with(None)
            sleeper.assert_not_called()

    def test_schedule_trigger_runs_only_on_main_first_attempt(self):
        for event in ("schedule", "workflow_dispatch", "push"):
            with self.subTest(event=event), patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
                "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": event,
            }):
                check_run_trigger()

    def test_duplicate_or_untrusted_scheduled_events_are_blocked(self):
        baseline_vars = {
            "GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "schedule",
        }
        for key, bad_value in (
            ("GITHUB_ACTIONS", "false"),
            ("GITHUB_REF", "refs/heads/test"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_EVENT_NAME", "pull_request"),
        ):
            with self.subTest(key=key), patch.dict(os.environ, {**baseline_vars, key: bad_value}):
                with self.assertRaisesRegex(RuntimeError, "NOT_MAIN_FIRST_ATTEMPT"):
                    check_run_trigger()

    def test_drain_publishes_all_three_in_fifo_order(self):
        state, _, _ = ingest(baseline(), [deal(94746), deal(94745), deal(94744)])
        store = FakeStore(state)
        submitted = []

        def publish(article):
            submitted.append(article["source_id"])
            return {
                "status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
                "post_url": f"https://cafe.daangn.com/posts/{article['source_id']}",
                "submit_attempted": True,
            }

        with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-drain"}), \
                patch("gh_only.jirum_manual_queue.build_next",
                      side_effect=lambda st: {"source_id": st["pending"][0]["id"]}), \
                patch("gh_only.jirum_manual_publisher.publish_one",
                      side_effect=publish):
            code = publish_pending(store, drain=True, clock=lambda: 0)

        self.assertEqual(code, 0)
        self.assertEqual(submitted, [94744, 94745, 94746])
        self.assertEqual(store.value["pending"], [])
        self.assertIsNone(store.value["in_flight"])
        self.assertTrue(all(store.value["published"][str(i)]["status"]
                            == "PUBLISHED_VERIFIED" for i in submitted))
        self.assertEqual(len(store.writes), 6)  # claim + finish per item

    def test_drain_stops_after_pre_submit_failure_no_infinite_retries(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745), deal(94746)])
        store = FakeStore(state)
        seen = []

        def publish(article):
            seen.append(article["source_id"])
            return {"status": "ABORTED_BEFORE_SUBMIT", "submit_attempted": False}

        with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-fail"}), \
                patch("gh_only.jirum_manual_queue.build_next",
                      side_effect=lambda st: {"source_id": st["pending"][0]["id"]}), \
                patch("gh_only.jirum_manual_publisher.publish_one",
                      side_effect=publish):
            code = publish_pending(store, drain=True, clock=lambda: 0)

        self.assertEqual(code, 2)
        self.assertEqual(seen, [94744])
        self.assertEqual([x["id"] for x in store.value["pending"]],
                         [94744, 94745, 94746])
        self.assertIsNone(store.value["in_flight"])

    def test_drain_uncertain_post_locks_queue_instead_of_replaying(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        store = FakeStore(state)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-uncertain"}), \
                patch("gh_only.jirum_manual_queue.build_next",
                      side_effect=lambda st: {"source_id": st["pending"][0]["id"]}), \
                patch("gh_only.jirum_manual_publisher.publish_one",
                      return_value={"status": "PUBLISH_UNCERTAIN_DO_NOT_RETRY",
                                    "submit_attempted": True}) as publish:
            code = publish_pending(store, drain=True, clock=lambda: 0)

        self.assertEqual(code, 2)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(store.value["in_flight"]["status"], "REVIEW_REQUIRED")
        self.assertEqual([x["id"] for x in store.value["pending"]], [94744, 94745])

    def test_drain_yields_safely_before_next_item_at_time_budget(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745), deal(94746)])
        store = FakeStore(state)
        clock_values = iter([0, 700])
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-budget"}), \
                patch("gh_only.jirum_manual_queue.build_next",
                      side_effect=lambda st: {"source_id": st["pending"][0]["id"]}), \
                patch("gh_only.jirum_manual_publisher.publish_one",
                      return_value={"status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
                                    "post_url": "https://cafe.daangn.com/posts/94744",
                                    "submit_attempted": True}) as publish:
            code = publish_pending(store, drain=True, clock=lambda: next(clock_values))

        self.assertEqual(code, 0)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual([x["id"] for x in store.value["pending"]], [94745, 94746])
        self.assertEqual(store.value["published"]["94744"]["status"], "PUBLISHED_VERIFIED")

    def test_single_publish_command_still_posts_only_one(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        store = FakeStore(state)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-one"}), \
                patch("gh_only.jirum_manual_queue.build_next",
                      side_effect=lambda st: {"source_id": st["pending"][0]["id"]}), \
                patch("gh_only.jirum_manual_publisher.publish_one",
                      return_value={"status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
                                    "post_url": "https://cafe.daangn.com/posts/94744",
                                    "submit_attempted": True}) as publish:
            code = publish_pending(store, drain=False, clock=lambda: 0)
        self.assertEqual(code, 0)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual([x["id"] for x in store.value["pending"]], [94745])

    def test_drain_empty_queue_does_not_touch_publisher(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-empty"}), \
                patch("gh_only.jirum_manual_queue.build_next") as build:
            code = publish_pending(FakeStore(baseline()), drain=True, clock=lambda: 0)
        self.assertEqual(code, 0)
        build.assert_not_called()

    def test_expired_kakao_secret_blocks_reruns_without_browsers(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        with patch.dict(os.environ, {
            "KAKAO_DEV_STORAGE_STATE_JSON": "expired-session-test-value"
        }):
            state["kakao_auth_block"] = {
                "reason": "KAKAO_LOGIN_EXPIRED", "source_id": 94744,
                "detected_at": "2026-11-02T03:00:00Z",
                "secret_fingerprint": kakao_secret_fingerprint(),
            }
            store = FakeStore(state)
            with patch("gh_only.jirum_manual_queue.collect_since",
                       return_value=[deal(94745)]):
                status = scan(store)
        self.assertEqual(status["status"], "KAKAO_REAUTH_REQUIRED")
        self.assertFalse(status["has_deal"])
        self.assertEqual(status["pending"], 2)
        self.assertEqual(store.value["last_scanned_id"], 94745)
        self.assertEqual(len(store.writes), 1)
        self.assertIsNone(store.value["in_flight"])
        self.assertNotIn("expired-session-test-value", json.dumps(store.value))

    def test_new_secret_unblocks_fifo_and_preserves_queued_posts(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        with patch.dict(os.environ, {
            "KAKAO_DEV_STORAGE_STATE_JSON": "old-kakao-secret"
        }):
            state["kakao_auth_block"] = {
                "reason": "KAKAO_LOGIN_EXPIRED", "source_id": 94744,
                "detected_at": "2026-11-02T03:00:00Z",
                "secret_fingerprint": kakao_secret_fingerprint(),
            }
        store = FakeStore(state)
        with patch.dict(os.environ, {
            "KAKAO_DEV_STORAGE_STATE_JSON": "new-kakao-secret"
        }), patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            status = scan(store)
        self.assertEqual(status["status"], "READY_ONE")
        self.assertTrue(status["has_deal"])
        self.assertNotIn("kakao_auth_block", store.value)
        self.assertEqual([r["id"] for r in store.value["pending"]], [94744, 94745])
        self.assertEqual(len(store.writes), 1)

    def test_kakao_login_expiry_failure_is_checkpointed_without_claim(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        store = FakeStore(state)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "drafts.json").write_text(json.dumps({
                "drafts": [{"kakao_status": "KAKAO_LOGIN_EXPIRED"}]
            }), encoding="utf-8")
            with patch.dict(os.environ, {
                "GITHUB_RUN_ID": "run-expired",
                "KAKAO_DEV_STORAGE_STATE_JSON": "expired-secret",
            }), patch("gh_only.jirum_manual_queue.DRAFT_DIR", folder), \
                    patch("gh_only.jirum_manual_queue.build_next",
                          side_effect=RuntimeError("POST_EVIDENCE_NOT_READY")), \
                    patch("gh_only.jirum_manual_publisher.publish_one") as publish:
                result = publish_pending(store, drain=True, clock=lambda: 0)
                self.assertEqual(
                    store.value["kakao_auth_block"]["secret_fingerprint"],
                    kakao_secret_fingerprint(),
                )
            self.assertEqual(result, 2)
            publish.assert_not_called()
        self.assertEqual([r["id"] for r in store.value["pending"]], [94744, 94745])
        self.assertIsNone(store.value["in_flight"])
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(store.value["kakao_auth_block"]["reason"], "KAKAO_LOGIN_EXPIRED")

    def test_nonlogin_evidence_failure_never_disables_future_runs(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        store = FakeStore(state)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "drafts.json").write_text(json.dumps({
                "drafts": [{"kakao_status": "KAKAO_METADATA_READ",
                            "photo": {"status": "IMAGE_INVALID"}}]
            }), encoding="utf-8")
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "run-bad-photo"}), \
                    patch("gh_only.jirum_manual_queue.DRAFT_DIR", folder), \
                    patch("gh_only.jirum_manual_queue.build_next",
                          side_effect=RuntimeError("POST_EVIDENCE_NOT_READY")):
                with self.assertRaisesRegex(RuntimeError, "POST_EVIDENCE_NOT_READY"):
                    publish_pending(store, drain=True, clock=lambda: 0)
        self.assertNotIn("kakao_auth_block", store.value)
        self.assertEqual(store.writes, [])

    def test_corrupt_kakao_block_rejected(self):
        s = baseline()
        s["kakao_auth_block"] = {
            "reason": "KAKAO_LOGIN_EXPIRED",
            "source_id": 94744,
            "detected_at": "now",
            "secret_fingerprint": "fake",
        }
        with self.assertRaisesRegex(ValueError, "INVALID_KAKAO_AUTH_BLOCK"):
            validate_state(s)

    def test_expire_archive_preserves_old_and_unknown_posts(self):
        reference = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)
        s, _, _ = ingest(baseline(), [deal(94744),deal(94745),deal(94746),deal(94747)])
        s["pending"][0]["posted_at_utc"] = (reference - timedelta(hours=4)).isoformat()
        s["pending"][1]["posted_at_utc"] = None
        s["pending"][2]["posted_at_utc"] = (reference + timedelta(hours=2)).isoformat()
        s["pending"][3]["posted_at_utc"] = (reference - timedelta(minutes=5)).isoformat()
        out, expired = expire_pending(s, reference=reference)
        self.assertEqual(expired, 3)
        self.assertEqual([x["id"] for x in out["pending"]], [94747])
        self.assertEqual(out["skipped_expired"]["94744"]["status"],
                         "DEAL_OVER_60_MINUTES_OLD")
        self.assertEqual(out["skipped_expired"]["94745"]["status"], "POSTED_AT_UNKNOWN")
        self.assertEqual(out["skipped_expired"]["94746"]["status"], "POSTED_AT_IN_FUTURE")
        self.assertNotIn("skipped_expired", s)
        self.assertEqual(len(s["pending"]), 4)
        validate_state(out)

    def test_lowchart_scan_backfills_latest_and_removes_old_kakao_lock(self):
        now = datetime.now(timezone.utc)
        s, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        with patch.dict(os.environ, {"KAKAO_DEV_STORAGE_STATE_JSON": "old-value"}):
            s["kakao_auth_block"] = {
                "reason":"KAKAO_LOGIN_EXPIRED", "source_id":94744,
                "detected_at":now.isoformat(),
                "secret_fingerprint":kakao_secret_fingerprint(),
            }
        store = FakeStore(s)
        recent = {"source_message_id":94745, "posted_at_utc":now.isoformat()}
        with patch.dict(os.environ, {
            "JIRUM_IMAGE_SOURCE":"lowchart", "JIRUM_REQUIRE_FRESH_POST":"true"
        }), patch("gh_only.jirum_manual_queue.collect_since", return_value=[]), \
                patch("gh_only.jirum_manual_queue.enrich_posts", return_value=[recent]):
            res = scan(store, latest_fetch=lambda: "<div>public</div>")
        self.assertTrue(res["has_deal"])
        self.assertEqual(res["source_id"], 94745)
        self.assertEqual(res["expired_without_post"], 1)
        self.assertNotIn("kakao_auth_block", store.value)
        self.assertEqual(store.value["skipped_expired"]["94744"]["status"],
                         "POSTED_AT_UNKNOWN")
        self.assertEqual(store.value["pending"][0]["posted_at_utc"], now.isoformat())
        self.assertEqual(len(store.writes), 1)

    def test_freshness_enforcement_does_not_post_untimed_legacy(self):
        now = datetime.now(timezone.utc)
        s, _, _ = ingest(baseline(), [deal(94744),deal(94745)])
        s["pending"][1]["posted_at_utc"] = now.isoformat()
        store = FakeStore(s)
        with patch.dict(os.environ, {
            "GITHUB_RUN_ID":"fresh-test",
            "JIRUM_REQUIRE_FRESH_POST":"true",
        }), patch("gh_only.jirum_manual_queue.build_next",
                  side_effect=lambda state: {"source_id":state["pending"][0]["id"]}), \
                patch("gh_only.jirum_manual_publisher.publish_one",
                      return_value={"status":"POST_PUBLISHED_AND_FORMAT_VERIFIED",
                                    "post_url":"https://cafe.daangn.com/posts/94745",
                                    "submit_attempted":True}) as pub:
            self.assertEqual(publish_pending(store, drain=True, clock=lambda:0),0)
        self.assertEqual(pub.call_count,1)
        self.assertIn("94744",store.value["skipped_expired"])
        self.assertEqual(store.value["published"]["94745"]["status"],"PUBLISHED_VERIFIED")
        self.assertIsNone(store.value["in_flight"])

    def test_freshness_boundaries_and_missing_timezone_are_rejected(self):
        now = datetime(2026,11,2,4,0,tzinfo=timezone.utc)
        self.assertEqual(freshness_status({
            "posted_at_utc":(now-timedelta(minutes=59)).isoformat()
        },reference=now),"FRESH")
        self.assertEqual(freshness_status({
            "posted_at_utc":(now-timedelta(minutes=61)).isoformat()
        },reference=now),"DEAL_OVER_60_MINUTES_OLD")
        self.assertEqual(freshness_status({
            "posted_at_utc":"2026-11-02T04:00:00"
        },reference=now),"POSTED_AT_UNKNOWN")

    def test_unverifiable_lowchart_photo_skips_only_bad_deal(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        store = FakeStore(state)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "drafts.json").write_text(json.dumps({
                "drafts": [{"image_metadata_status":
                            "LOWCHART_TELEGRAM_TITLE_MISMATCH"}]
            }), encoding="utf-8")
            def build(current):
                if current["pending"][0]["id"] == 94744:
                    raise RuntimeError("POST_EVIDENCE_NOT_READY")
                return {"source_id": current["pending"][0]["id"]}
            with patch.dict(os.environ, {
                "GITHUB_RUN_ID":"skip-unverifiable",
                "JIRUM_IMAGE_SOURCE":"lowchart",
            }), patch("gh_only.jirum_manual_queue.DRAFT_DIR", folder), \
                    patch("gh_only.jirum_manual_queue.build_next", side_effect=build), \
                    patch("gh_only.jirum_manual_publisher.publish_one",
                          return_value={"status":"POST_PUBLISHED_AND_FORMAT_VERIFIED",
                                        "submit_attempted":True,
                                        "post_url":"https://cafe.daangn.com/posts/94745"}) as publisher:
                self.assertEqual(publish_pending(store, drain=True, clock=lambda:0),0)
        self.assertIn("94744", store.value["skipped_unverifiable"])
        self.assertEqual(store.value["published"]["94745"]["status"],"PUBLISHED_VERIFIED")
        self.assertIsNone(store.value["in_flight"])
        self.assertEqual(store.value["pending"],[])
        publisher.assert_called_once()

    def test_lowchart_transport_error_is_audited_and_next_item_publishes(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        store = FakeStore(state)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "drafts.json").write_text(json.dumps({
                "drafts": [{
                    "product_identity": {"status": "EXACT_ITEM_IDENTIFIED"},
                    "image_metadata_status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                    "photo": {"status": "NO_MATCHING_PRODUCT_IMAGE"},
                }]
            }), encoding="utf-8")
            def build(current):
                if current["pending"][0]["id"] == 94744:
                    raise RuntimeError("POST_EVIDENCE_NOT_READY")
                return {"source_id": current["pending"][0]["id"]}
            with (patch.dict(os.environ, {
                    "GITHUB_RUN_ID": "skip-image-error",
                    "JIRUM_IMAGE_SOURCE": "lowchart"}),
                  patch("gh_only.jirum_manual_queue.DRAFT_DIR", folder),
                  patch("gh_only.jirum_manual_queue.build_next", side_effect=build),
                  patch("gh_only.jirum_manual_publisher.publish_one", return_value={
                      "status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
                      "submit_attempted": True,
                      "post_url": "https://cafe.daangn.com/posts/94745",
                  }) as publisher):
                self.assertEqual(publish_pending(store, drain=True, clock=lambda: 0), 0)
        self.assertEqual(store.value["skipped_unverifiable"]["94744"]["status"],
                         "PHOTO_EVIDENCE_UNAVAILABLE")
        self.assertEqual(store.value["published"]["94745"]["status"],
                         "PUBLISHED_VERIFIED")
        self.assertEqual(store.value["pending"], [])
        publisher.assert_called_once()

    def test_original_photo_candidate_is_saved_with_queued_item(self):
        source = deal(94744)
        source["image_provenance"] = "telegram_attached_photo"
        source["image_url_candidate"] = "https://cdn4.telesco.pe/file/source.jpg"
        state, _, _ = ingest(baseline(), [source])
        self.assertEqual(state["pending"][0]["image_provenance"],
                         "telegram_attached_photo")
        self.assertEqual(state["pending"][0]["image_url_candidate"],
                         "https://cdn4.telesco.pe/file/source.jpg")

    def test_lowchart_photo_passes_real_pre_submit_draft_verifier(self):
        from gh_only.daangn_editor_probe import read_one_draft
        from gh_only.jirum_manual_queue import validate_draft
        from gh_only.test_daangn_editor_probe import DaangnEditorOfflineTests
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            report = DaangnEditorOfflineTests()._make_draft(folder)
            row = report["drafts"][0]
            old = folder / row["photo"]["saved_name"]
            row["photo"]["saved_name"] = "jirum_94717_lowchart_photo.jpg"
            old.rename(folder / row["photo"]["saved_name"])
            row.update({
                "image_product_match":True,
                "image_source":"lowchart_exact_item_public_page",
                "image_exact_item_page_verified":True,
                "lowchart_title_verified":True,
                "lowchart_product_title":"휘카레 셰프 오리지널 소스 순한맛",
                "product_name_source":"lowchart_exact_item_page",
                "kakao_product_match":False,
                "product_identity":{
                    "status":"EXACT_ITEM_IDENTIFIED",
                    "product_id":"9217210892","item_id":"27232840915",
                },
            })
            row["caption"]["sale_price_krw"] = 12050
            row["caption"]["product_name"] = "휘카레 셰프 오리지널 소스 순한맛"
            (folder / "drafts.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )
            with patch.dict(os.environ, {
                "JIRUM_IMAGE_SOURCE":"lowchart",
                "COUPANG_PARTNER_LPTAG":"AF9668915"
            }):
                out = validate_draft(folder, 94717)
            self.assertEqual(out["source_id"],94717)
            self.assertEqual(out["image"].name,"jirum_94717_lowchart_photo.jpg")
            self.assertEqual(out["product_id"],"9217210892")

    def test_unverified_lowchart_name_blocks_even_with_source_photo(self):
        from gh_only.jirum_manual_queue import validate_draft, permanent_lowchart_failure
        from gh_only.test_daangn_editor_probe import DaangnEditorOfflineTests
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            report = DaangnEditorOfflineTests()._make_draft(folder)
            row = report["drafts"][0]
            row.update({
                "product_identity": {"status": "EXACT_ITEM_IDENTIFIED",
                                     "product_id": "9217210892", "item_id": "27232840915"},
                "image_source": "telegram_attached_photo",
                "source_photo_verified": True,
                "image_product_match": False,
                "lowchart_title_verified": False,
                "product_name_source": "unverified_fallback",
                "photo": {"status": "IMAGE_SAVED",
                          "saved_name": "jirum_94717_telegram_source_photo.jpg",
                          "image_source": "telegram_attached_photo"},
            })
            (folder / "jirum_94717_kakao_og.jpg").rename(
                folder / "jirum_94717_telegram_source_photo.jpg")
            (folder / "drafts.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8")
            with patch.dict(os.environ, {
                "JIRUM_IMAGE_SOURCE": "lowchart",
                "COUPANG_PARTNER_LPTAG": "AF9668915",
            }):
                with self.assertRaisesRegex(RuntimeError, "POST_EVIDENCE_NOT_READY"):
                    validate_draft(folder, 94717)
                self.assertEqual(permanent_lowchart_failure(folder),
                                 "LOWCHART_TITLE_UNVERIFIED")

    def test_fifo_and_non_coupang_filter(self):
        state, n, ignored = ingest(baseline(), [
            deal(94747), deal(94745, "external"), deal(94746),
            deal(94744, "external")
        ])
        self.assertEqual(n, 2)
        self.assertEqual(ignored, 2)
        self.assertEqual([x["id"] for x in state["pending"]], [94746, 94747])
        self.assertEqual(state["last_scanned_id"], 94747)

    def test_no_new_messages_no_publish(self):
        store = FakeStore(baseline())
        with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertEqual(result["status"], "NO_NEW_COUPANG_DEAL")
        self.assertFalse(result["has_deal"])
        self.assertEqual(store.writes, [])

    def test_external_only_advances_watermark_without_post(self):
        st, count, ignored = ingest(baseline(), [deal(94744, "external")])
        self.assertEqual((count, ignored), (0, 1))
        self.assertEqual(st["pending"], [])
        self.assertEqual(st["last_scanned_id"], 94744)

    def test_coupang_backlog_oldest_first_one_at_a_time(self):
        st, _, _ = ingest(baseline(), [deal(94744), deal(94745), deal(94746)])
        store = FakeStore(st)
        store.value["in_flight"] = {
            "id": 94744, "status": "CLAIMED",
            "run_id": "run1", "at": "now",
        }
        finish(store, 94744, "run1", {
            "status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
            "post_url": "https://cafe.daangn.com/i-gagyeog-mos-c/posts/posted44",
        })
        self.assertEqual([x["id"] for x in store.value["pending"]], [94745, 94746])
        self.assertEqual(store.value["published"]["94744"]["status"], "PUBLISHED_VERIFIED")
        self.assertIsNone(store.value["in_flight"])
        with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertEqual(result["source_id"], 94745)
        self.assertEqual(result["pending"], 2)

    def test_unverified_after_attempt_blocks_all_future_work(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        state["in_flight"] = {"id": 94744, "status": "CLAIMED", "run_id": "run9", "at": "now"}
        store = FakeStore(state)
        finish(store, 94744, "run9", {
            "status": "PUBLISH_UNCERTAIN_DO_NOT_RETRY",
            "submit_attempted": True,
        })
        self.assertEqual(store.value["in_flight"]["status"], "REVIEW_REQUIRED")
        self.assertEqual(store.value["pending"][0]["id"], 94744)
        with patch("gh_only.jirum_manual_queue.collect_since") as fetch:
            result = scan(store)
        fetch.assert_not_called()
        self.assertFalse(result["has_deal"])

    def test_aborted_before_submit_retries_same_head_only(self):
        st, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        st["in_flight"] = {"id": 94744, "status": "CLAIMED", "run_id": "run10", "at": "now"}
        store = FakeStore(st)
        finish(store, 94744, "run10", {
            "status": "ABORTED_BEFORE_SUBMIT", "submit_attempted": False,
        })
        self.assertIsNone(store.value["in_flight"])
        self.assertEqual([r["id"] for r in store.value["pending"]], [94744, 94745])
        with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertEqual(result["source_id"], 94744)

    def test_do_not_reprocess_already_published_id(self):
        st, n, _ = ingest(baseline(), [deal(94743), deal(94744)])
        self.assertEqual(n, 1)
        st2, n2, _ = ingest(st, [deal(94743), deal(94744)])
        self.assertEqual(n2, 0)
        self.assertEqual([x["id"] for x in st2["pending"]], [94744])

    def test_contradictory_in_flight_and_published_is_rejected(self):
        st, _, _ = ingest(baseline(), [deal(94744)])
        st["published"]["94744"] = {"status": "PUBLISHED_VERIFIED"}
        with self.assertRaisesRegex(ValueError, "PUBLISHED_ITEM_STILL_QUEUED"):
            validate_state(st)

    def test_collect_since_reads_older_pages_without_skipping_backlog(self):
        lookup = {
            "latest": [deal(94749), deal(94750)],
            "older94749": [deal(94746), deal(94747), deal(94748)],
            "older94746": [deal(94742), deal(94743), deal(94744), deal(94745)],
        }
        with patch("gh_only.jirum_manual_queue.enrich_posts",
                   side_effect=lambda html, max_posts: lookup[html]):
            pages = collect_since(94743, latest_fetch=lambda: "latest",
                                  before_fetch=lambda x: "older" + str(x))
        self.assertEqual([x["source_message_id"] for x in pages],
                         [94744, 94745, 94746, 94747, 94748, 94749, 94750])

    def test_history_not_complete_is_error_not_skipped(self):
        with patch("gh_only.jirum_manual_queue.enrich_posts",
                   return_value=[deal(94800)]):
            with self.assertRaisesRegex(RuntimeError, "HISTORY_NOT_ADVANCING"):
                collect_since(94743, latest_fetch=lambda: "latest",
                              before_fetch=lambda x: "same", max_pages=3)

    def test_oversized_fifo_does_not_advance_cursor(self):
        with self.assertRaisesRegex(RuntimeError, "OVER_LIMIT"):
            ingest(baseline(), [deal(i) for i in range(94744, 95046)])


if __name__ == "__main__":
    unittest.main()
