"""Offline regression checks for Naver queue segregation and safe source filtering."""
from datetime import datetime, timedelta, timezone
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from gh_only.naver_blog_jirum import (
    ingest, validate_state, source_fresh, validate_naver_link_subid,
    naver_title, archive_stale_claim, NAVER_TITLE_MAX_UTF16_UNITS,
    NaverStateConflict, scan, scan_with_source_wait, claim_before_publish, checkpoint_published,
    publish_batch, expire_unclaimed_before_next, wait_for_batch_checkpoint,
    apply_official_api_link, make_article, publish_one,
    _photo_unavailable_before_submit, NaverPreSubmitPhotoUnavailable,
    checkpoint_pre_submit_photo_unavailable,
    checkpoint_pre_submit_title_failure, NaverPreSubmitTitleDeferred,
)


def state():
    return {
        "schema": "naver-blog-jirum-fifo-v1",
        "last_scanned_id": 95495, "pending": [], "published": {},
        "skipped": {}, "in_flight": None,
    }


def deal(source_id, *, price=11900, minutes=10, kind="coupang_shortlink"):
    posted = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    return {
        "source_message_id": source_id, "source_post_url": f"https://t.me/jirum/{source_id}",
        "original_purchase_url": "https://link.coupang.com/a/example",
        "source_text": "샘플 가격 11,900원", "title": "샘플",
        "sale_price_krw": price, "url_kind": kind, "posted_at_utc": posted,
    }


from gh_only.naver_blog_deal_publisher import NaverPreSubmitTitleError


class ConflictStore:
    """Simulates a second workflow advancing the GitHub branch head."""
    def __init__(self, initial=None, conflicts=1):
        self.current = deepcopy(initial if initial is not None else state())
        self.conflicts = conflicts
        self.write_count = 0
        self.read_count = 0

    def read(self):
        self.read_count += 1
        return deepcopy(self.current), f"sha-{self.read_count}"

    def write(self, next_state, sha, message):
        self.write_count += 1
        if self.write_count <= self.conflicts:
            # The OTHER workflow updated state independently. A fresh
            # read/recompute must preserve this new entry.
            self.current["published"]["95490"] = {
                "url": "https://blog.naver.com/mw5114/1",
                "at": "2026-11-10T00:00:00Z",
            }
            raise NaverStateConflict("NAVER_STATE_KNOWN_409_REBASE")
        self.current = deepcopy(next_state)


class NaverQueueTests(unittest.TestCase):
    def test_pre_submit_title_failure_deferred_then_recovers(self):
        s = state()
        ingest(s, [deal(95496)])
        store = ConflictStore(initial=s, conflicts=0)
        def prepared(_store):
            item, _ = _store.read()
            return {"source_id": item["pending"][0]["id"], "unissued_link": False}
        url = "https://blog.naver.com/mw5114/224485999999"
        with (patch.dict(os.environ, {"GITHUB_RUN_ID": "first-title-run"}),
              patch("gh_only.naver_blog_jirum.prepare_next", side_effect=prepared),
              patch("gh_only.naver_blog_deal_publisher.submit_one",
                    side_effect=NaverPreSubmitTitleError("NAVER_TITLE_NOT_APPLIED")) as submit):
            self.assertEqual(publish_batch(store, max_posts=1), 0)
            submit.assert_called_once()
        self.assertEqual(store.current["pending"][0]["title_prepare_failure_count"], 1)
        self.assertNotIn("95496", store.current["skipped"])
        self.assertIsNone(store.current["in_flight"])
        with (patch.dict(os.environ, {"GITHUB_RUN_ID": "second-title-run"}),
              patch("gh_only.naver_blog_jirum.prepare_next", side_effect=prepared),
              patch("gh_only.naver_blog_deal_publisher.submit_one",
                    return_value=url) as submit):
            self.assertEqual(publish_batch(store, max_posts=1), 0)
            submit.assert_called_once()
        self.assertEqual(store.current["published"]["95496"]["url"], url)
        self.assertEqual(store.current["pending"], [])
        self.assertIsNone(store.current["in_flight"])

    def test_second_title_failure_skips_only_bad_item(self):
        s = state()
        ingest(s, [deal(95496), deal(95497)])
        store = ConflictStore(initial=s, conflicts=0)
        def prepared(_store):
            item, _ = _store.read()
            return {"source_id": item["pending"][0]["id"], "unissued_link": False}
        with (patch.dict(os.environ, {"GITHUB_RUN_ID": "title-run-1"}),
              patch("gh_only.naver_blog_jirum.prepare_next", side_effect=prepared),
              patch("gh_only.naver_blog_deal_publisher.submit_one",
                    side_effect=NaverPreSubmitTitleError("NAVER_TITLE_NOT_APPLIED"))):
            self.assertEqual(publish_batch(store, max_posts=1), 0)
        url = "https://blog.naver.com/mw5114/224485999998"
        with (patch.dict(os.environ, {"GITHUB_RUN_ID": "title-run-2"}),
              patch("gh_only.naver_blog_jirum.prepare_next", side_effect=prepared),
              patch("gh_only.naver_blog_jirum.time.sleep"),
              patch("gh_only.naver_blog_deal_publisher.submit_one",
                    side_effect=[NaverPreSubmitTitleError("NAVER_TITLE_NOT_APPLIED"),
                                 url]) as submit):
            self.assertEqual(publish_batch(store, max_posts=2), 0)
            self.assertEqual(submit.call_count, 2)
        self.assertEqual(store.current["skipped"]["95496"]["reason"],
                         "DEFINITE_PRE_SUBMIT_TITLE_REJECTED_NO_POST")
        self.assertEqual(store.current["published"]["95497"]["url"], url)
        self.assertEqual(store.current["pending"], [])
        self.assertIsNone(store.current["in_flight"])

    def test_title_deferral_rejects_foreign_claim_without_write(self):
        s = state()
        ingest(s, [deal(95496)])
        s["in_flight"] = {
            "id": 95496, "status": "REVIEW_REQUIRED",
            "run_id": "another-run", "at": datetime.now(timezone.utc).isoformat(),
        }
        store = ConflictStore(initial=s, conflicts=0)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "our-run"}):
            with self.assertRaisesRegex(RuntimeError, "CLAIM_CHANGED"):
                checkpoint_pre_submit_title_failure(store, 95496)
        self.assertEqual(store.write_count, 0)
        self.assertIsNotNone(store.current["in_flight"])

    def test_unknown_publisher_error_preserves_review_claim(self):
        s = state()
        ingest(s, [deal(95496)])
        store = ConflictStore(initial=s, conflicts=0)
        with (patch.dict(os.environ, {"GITHUB_RUN_ID": "unknown-post-run"}),
              patch("gh_only.naver_blog_jirum.prepare_next",
                    return_value={"source_id": 95496, "unissued_link": False}),
              patch("gh_only.naver_blog_deal_publisher.submit_one",
                    side_effect=RuntimeError("SUBMISSION_UNCERTAIN_DO_NOT_RETRY"))):
            with self.assertRaisesRegex(RuntimeError, "SUBMISSION_UNCERTAIN"):
                publish_batch(store, max_posts=1)
        self.assertEqual(store.current["in_flight"]["id"], 95496)
        self.assertEqual(store.current["pending"][0]["id"], 95496)
        self.assertNotIn("95496", store.current["skipped"])

    def test_draft_missing_photo_is_precisely_classified_pre_submit(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            folder = Path(td)
            row = {
                "source_message_id": 96223,
                "product_identity": {
                    "status": "EXACT_ITEM_IDENTIFIED",
                    "product_id": "9378826778",
                    "item_id": "28420179798",
                },
                "image_metadata_status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                "photo": {"status": "PHOTO_EVIDENCE_UNAVAILABLE",
                          "saved_name": None},
                "photo_provider_attempts": [
                    {"provider": "fallcent_search",
                     "status": "FALLCENT_NO_MATCHING_IMAGE"},
                    {"provider": "naver_web",
                     "status": "NAVER_SEARCH_REQUEST_FAILED"},
                ],
            }
            (folder / "drafts.json").write_text(
                json.dumps({"drafts": [row]}), encoding="utf-8")
            self.assertTrue(_photo_unavailable_before_submit(folder, 96223))
            self.assertFalse(_photo_unavailable_before_submit(folder, 96224))
            row["photo"]["status"] = "IMAGE_SAVED"
            (folder / "drafts.json").write_text(
                json.dumps({"drafts": [row]}), encoding="utf-8")
            self.assertFalse(_photo_unavailable_before_submit(folder, 96223))

    def test_only_missing_photo_pre_submit_becomes_safe_retryable_exception(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        source = deal(96223)
        source["source_message_id"] = 96223
        row = {
            "source_message_id": 96223,
            "product_identity": {
                "status": "EXACT_ITEM_IDENTIFIED",
                "product_id": "9378826778",
                "item_id": "28420179798",
            },
            "image_metadata_status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
            "photo": {"status": "PHOTO_EVIDENCE_UNAVAILABLE",
                      "saved_name": None},
            "photo_provider_attempts": [{"provider": "naver_web",
                                          "status": "NAVER_SEARCH_REQUEST_FAILED"}],
        }
        with TemporaryDirectory() as td, \
             patch("gh_only.naver_blog_jirum.enrich_deal", return_value=row), \
             patch("gh_only.naver_blog_jirum.old.validate_draft",
                   side_effect=RuntimeError("POST_EVIDENCE_NOT_READY")):
            with self.assertRaises(NaverPreSubmitPhotoUnavailable) as cm:
                make_article(source, Path(td), tag="AF9668915")
            self.assertEqual(cm.exception.source_id, 96223)
        # An unrelated safety failure must still be fatal, not ignored.
        with TemporaryDirectory() as td, \
             patch("gh_only.naver_blog_jirum.enrich_deal", return_value=row), \
             patch("gh_only.naver_blog_jirum.old.validate_draft",
                   side_effect=RuntimeError("WRONG_SKU_VALIDATION_FAILED")):
            with self.assertRaisesRegex(RuntimeError, "WRONG_SKU_VALIDATION_FAILED"):
                make_article(source, Path(td), tag="AF9668915")

    def test_item_expiring_during_photo_prep_is_safely_archived(self):
        s = state()
        ingest(s, [deal(95496, minutes=10), deal(95497, minutes=5)])
        store = ConflictStore(initial=s, conflicts=0)
        def expire_then_raise(_store, *, with_receipt=False):
            store.current["pending"][0]["posted_at_utc"] = (
                datetime.now(timezone.utc) - timedelta(minutes=90)
            ).isoformat()
            raise RuntimeError("NAVER_OLDEST_POST_EXPIRED_RESCAN")
        with (patch("gh_only.naver_blog_jirum.publish_one",
                    side_effect=expire_then_raise) as publish,
              patch("gh_only.naver_blog_jirum.claim_before_publish") as claim):
            self.assertEqual(publish_batch(store, max_posts=1), 0)
        self.assertEqual(publish.call_count, 1)
        claim.assert_not_called()
        self.assertIsNone(store.current["in_flight"])
        self.assertEqual(store.current["pending"][0]["id"], 95497)
        self.assertEqual(store.current["skipped"]["95496"]["reason"],
                         "STALE_OR_UNKNOWN_TIMESTAMP")
        self.assertNotIn("95496", store.current["published"])

    def test_non_expiry_pre_submit_error_still_fails_closed(self):
        s = state()
        ingest(s, [deal(95496, minutes=10)])
        store = ConflictStore(initial=s, conflicts=0)
        with patch("gh_only.naver_blog_jirum.publish_one",
                   side_effect=RuntimeError("UNVERIFIED_ITEM_IDENTITY")):
            with self.assertRaisesRegex(RuntimeError, "UNVERIFIED_ITEM_IDENTITY"):
                publish_batch(store, max_posts=1)
        self.assertEqual(store.current["pending"][0]["id"], 95496)
        self.assertNotIn("95496", store.current["published"])
        self.assertIsNone(store.current["in_flight"])

    def test_two_failed_runs_defer_then_archive_without_ever_claiming(self):
        s = state()
        ingest(s, [deal(95496), deal(95497)])
        store = ConflictStore(initial=s, conflicts=0)
        exc = NaverPreSubmitPhotoUnavailable(95496)
        with patch("gh_only.naver_blog_jirum.publish_one",
                   side_effect=exc), \
             patch("gh_only.naver_blog_jirum.claim_before_publish") as claim:
            self.assertEqual(publish_batch(store, max_posts=1), 0)
            self.assertEqual(store.current["pending"][0]["id"], 95496)
            self.assertEqual(
                store.current["pending"][0]["photo_prepare_failure_count"], 1)
            self.assertNotIn("95496", store.current["skipped"])
            self.assertIsNone(store.current["in_flight"])
            # Next separate Oracle run also gets no verified image.
            self.assertEqual(publish_batch(store, max_posts=1), 0)
        claim.assert_not_called()
        self.assertNotIn("95496", store.current["published"])
        self.assertEqual(
            store.current["skipped"]["95496"]["reason"],
            "NO_VERIFIED_PHOTO_AFTER_TWO_PRE_SUBMIT_RUNS")
        self.assertEqual([x["id"] for x in store.current["pending"]], [95497])
        self.assertIsNone(store.current["in_flight"])

    def test_pre_submit_photo_deferral_rebases_known_409_without_reposting(self):
        s = state()
        ingest(s, [deal(95496)])
        store = ConflictStore(initial=s, conflicts=1)
        with patch("gh_only.naver_blog_jirum.time.sleep"):
            self.assertEqual(
                checkpoint_pre_submit_photo_unavailable(store, 95496),
                "DEFERRED")
        self.assertEqual(store.write_count, 2)
        self.assertEqual(store.current["pending"][0]["photo_prepare_failure_count"], 1)
        self.assertIsNone(store.current["in_flight"])
        self.assertNotIn("95496", store.current["published"])

    def test_pre_submit_photo_never_excuses_inflight_claim_or_wrong_source(self):
        s = state()
        ingest(s, [deal(95496)])
        store = ConflictStore(initial=s, conflicts=0)
        with self.assertRaisesRegex(RuntimeError, "STATE_CHANGED"):
            checkpoint_pre_submit_photo_unavailable(store, 95497)
        store.current["in_flight"] = {
            "id": 95496, "status": "REVIEW_REQUIRED",
            "run_id": "test", "at": datetime.now(timezone.utc).isoformat()
        }
        with self.assertRaisesRegex(RuntimeError, "STATE_CHANGED"):
            checkpoint_pre_submit_photo_unavailable(store, 95496)
        self.assertEqual(store.write_count, 0)

    def test_publisher_retries_photo_only_before_claim_once(self):
        source_id = 95496
        article = {
            "source_id": source_id,
            "title": "[쿠팡🔥핫딜] 샘플",
            "image": object(), "link": "https://link.coupang.com/a/AbcDEF1",
            "unissued_link": False,
        }
        store = ConflictStore(conflicts=0)
        with patch("gh_only.naver_blog_jirum.prepare_next",
                   side_effect=[NaverPreSubmitPhotoUnavailable(source_id),
                                article]) as prepare, \
             patch("gh_only.naver_blog_jirum.time.sleep") as sleep, \
             patch("gh_only.naver_blog_jirum.claim_before_publish") as claim, \
             patch("gh_only.naver_blog_jirum.checkpoint_published") as saved, \
             patch("gh_only.naver_blog_deal_publisher.submit_one",
                   return_value="https://blog.naver.com/mw5114/224477999999") as submit:
            receipt = publish_one(store, with_receipt=True)
        self.assertEqual(prepare.call_count, 2)
        sleep.assert_called_once_with(7)
        claim.assert_called_once_with(store, source_id)
        submit.assert_called_once()
        saved.assert_called_once()
        self.assertEqual(receipt["source_id"], source_id)

    def test_oracle_public_lag_retries_until_exact_source_visible(self):
        # 2026-11-15 real case: 96111 arrived in Telegram's public HTML
        # 42 seconds after its event; Naver initially saw only 96110.
        replies = iter((
            {"ready": False, "last_scanned_id": 96110, "pending": 0},
            {"ready": False, "last_scanned_id": 96110, "pending": 0},
            {"ready": True, "last_scanned_id": 96111, "pending": 1},
        ))
        checks, sleeps = [], []
        def fake_scan(store):
            checks.append(store)
            return next(replies)
        result = scan_with_source_wait(
            "fake-store", 96111, scanner=fake_scan, sleeper=sleeps.append)
        self.assertEqual(result["last_scanned_id"], 96111)
        self.assertTrue(result["ready"])
        self.assertEqual(checks, ["fake-store"] * 3)
        self.assertEqual(sleeps, [20, 20])

    def test_backup_scan_without_source_id_never_waits(self):
        seen, sleeps = [], []
        result = scan_with_source_wait(
            "fake-store", 0,
            scanner=lambda store: (seen.append(store) or
                                   {"ready": False, "last_scanned_id": 96110}),
            sleeper=sleeps.append,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(sleeps, [])

    def test_public_lag_exhausted_does_not_advance_cursor_or_publish(self):
        checks, sleeps = [], []
        def no_new_post(store):
            checks.append(store)
            return {"ready": False, "pending": 0, "last_scanned_id": 96110}
        result = scan_with_source_wait(
            "fake-store", 96111, scanner=no_new_post, sleeper=sleeps.append)
        self.assertEqual(len(checks), 4)
        self.assertEqual(sleeps, [20, 20, 20])
        self.assertEqual(result["last_scanned_id"], 96110)
        self.assertFalse(result["ready"])

    def test_review_or_seen_source_stops_waiting_without_duplicates(self):
        checks, sleeps = [], []
        result = scan_with_source_wait(
            "fake-store", 96111,
            scanner=lambda store: (checks.append(store) or
                                   {"ready": False, "review": True}),
            sleeper=sleeps.append)
        self.assertEqual(len(checks), 1)
        self.assertTrue(result["review"])
        self.assertEqual(sleeps, [])
        result = scan_with_source_wait(
            "fake-store", 96111,
            scanner=lambda store: {"ready": False, "last_scanned_id": 96111},
            sleeper=sleeps.append)
        self.assertFalse(result["ready"])
        self.assertEqual(sleeps, [])

    def test_bad_expected_source_ids_fail_closed(self):
        for expected in (-1, True, "96111", 2**31):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                scan_with_source_wait("fake-store", expected, scanner=lambda s: None)

    def test_ready_backlog_does_not_wait_for_newer_oracle_event(self):
        ready = {"ready": True, "last_scanned_id": 96110, "pending": 1}
        with patch("gh_only.naver_blog_jirum.time.sleep") as sleep:
            result = scan_with_source_wait("fake-store", 96111, scanner=lambda _: ready)
        self.assertEqual(result, ready)
        sleep.assert_not_called()

    def test_batch_rescans_once_to_catch_new_arrival_after_verified_post(self):
        s = state()
        ingest(s, [deal(95496, minutes=5)])
        store = ConflictStore(initial=s, conflicts=0)
        order = []
        def confirmed_publish(current, *, with_receipt=False):
            row, _ = current.read()
            mid = row["pending"][0]["id"]
            url = f"https://blog.naver.com/mw5114/{224472000000 + mid}"
            claim_before_publish(current, mid)
            checkpoint_published(current, mid, url)
            order.append(mid)
            return {"source_id": mid, "url": url}
        def new_arrival(current):
            row, sha = current.read()
            self.assertIn("95496", row["published"])
            ingest(row, [deal(95497, minutes=5)])
            current.write(row, sha, "test new arrival")
            return {"ready": True}
        with patch("gh_only.naver_blog_jirum.publish_one", side_effect=confirmed_publish), patch("gh_only.naver_blog_jirum.time.sleep") as sleep, patch("gh_only.naver_blog_jirum.scan", side_effect=new_arrival) as rescan:
            self.assertEqual(publish_batch(store, rescan=rescan), 0)
        self.assertEqual(order, [95496, 95497])
        rescan.assert_called_once_with(store)
        sleep.assert_called_once()
        self.assertEqual(store.current["pending"], [])
        self.assertIsNone(store.current["in_flight"])

    def test_batch_oldest_first_max_six_with_gap(self):
        s = state()
        ingest(s, [deal(mid, minutes=5) for mid in range(95496, 95503)])
        store = ConflictStore(initial=s, conflicts=0)
        order = []
        def confirmed_publish(current, *, with_receipt=False):
            row, _ = current.read()
            mid = row["pending"][0]["id"]
            url = f"https://blog.naver.com/mw5114/{224472000000 + mid}"
            claim_before_publish(current, mid)
            checkpoint_published(current, mid, url)
            order.append(mid)
            return {"source_id": mid, "url": url}
        with patch("gh_only.naver_blog_jirum.publish_one",
                   side_effect=confirmed_publish) as publish, patch(
                "gh_only.naver_blog_jirum.time.sleep") as sleep:
            self.assertEqual(publish_batch(store), 0)
        self.assertEqual(order, [95496, 95497, 95498, 95499, 95500, 95501])
        self.assertEqual([r["id"] for r in store.current["pending"]], [95502])
        self.assertEqual(publish.call_count, 6)
        self.assertEqual(sleep.call_count, 5)
        self.assertIsNone(store.current["in_flight"])

    def test_batch_skips_expired_oldest_and_posts_next(self):
        s = state()
        earlier = datetime.now(timezone.utc) - timedelta(minutes=2)
        ingest(s, [deal(95496, minutes=61), deal(95497, minutes=5)],
               reference=earlier)
        store = ConflictStore(initial=s, conflicts=0)
        order = []
        def confirmed_publish(current, *, with_receipt=False):
            row, _ = current.read()
            mid = row["pending"][0]["id"]
            order.append(mid)
            url = "https://blog.naver.com/mw5114/224472999999"
            claim_before_publish(current, mid)
            checkpoint_published(current, mid, url)
            return {"source_id": mid, "url": url}
        with patch("gh_only.naver_blog_jirum.publish_one",
                   side_effect=confirmed_publish), patch(
                "gh_only.naver_blog_jirum.time.sleep"):
            self.assertEqual(publish_batch(store), 0)
        self.assertEqual(order, [95497])
        self.assertEqual(store.current["skipped"]["95496"]["reason"],
                         "STALE_OR_UNKNOWN_TIMESTAMP")
        self.assertEqual(store.current["pending"], [])

    def test_batch_uncertain_submit_halts_without_replay(self):
        s = state()
        ingest(s, [deal(95496), deal(95497)])
        store = ConflictStore(initial=s, conflicts=0)
        def uncertain(current, *, with_receipt=False):
            claim_before_publish(current, 95496)
            raise RuntimeError("SUBMISSION_UNCERTAIN_DO_NOT_RETRY")
        with patch("gh_only.naver_blog_jirum.publish_one",
                   side_effect=uncertain) as publish:
            with self.assertRaisesRegex(RuntimeError,
                                        "SUBMISSION_UNCERTAIN_DO_NOT_RETRY"):
                publish_batch(store)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(store.current["in_flight"]["id"], 95496)
        self.assertEqual([r["id"] for r in store.current["pending"]],
                         [95496, 95497])

    def test_batch_does_not_publish_with_uncertain_claim(self):
        s = state()
        ingest(s, [deal(95496)])
        s["in_flight"] = {"id": 95496, "status": "REVIEW_REQUIRED",
                          "run_id": "previous", "at": datetime.now(timezone.utc).isoformat()}
        store = ConflictStore(initial=s, conflicts=0)
        with patch("gh_only.naver_blog_jirum.publish_one") as publish:
            with self.assertRaisesRegex(RuntimeError, "NEEDS_MANUAL_REVIEW"):
                publish_batch(store)
        publish.assert_not_called()

    def test_checkpoint_visibility_retries_stale_get_without_reposting(self):
        s = state()
        ingest(s, [deal(95496)])
        original = deepcopy(s)
        url = "https://blog.naver.com/mw5114/224472901234"
        done = deepcopy(s)
        done["pending"] = []
        done["published"]["95496"] = {"url": url,
                                         "at": datetime.now(timezone.utc).isoformat()}
        class LaggedStore:
            def __init__(self):
                self.read_count = 0
            def read(self):
                self.read_count += 1
                result = original if self.read_count <= 3 else done
                return deepcopy(result), str(self.read_count)
        slow = LaggedStore()
        with patch("gh_only.naver_blog_jirum.time.sleep") as sleep:
            self.assertTrue(wait_for_batch_checkpoint(slow, 95496, url, max_reads=5))
        self.assertEqual(slow.read_count, 4)
        self.assertEqual(sleep.call_count, 3)

    def test_checkpoint_visibility_timeout_stops_safely_without_retry(self):
        s = state()
        ingest(s, [deal(95496)])
        store = ConflictStore(initial=s, conflicts=0)
        url = "https://blog.naver.com/mw5114/224472901234"
        with patch("gh_only.naver_blog_jirum.time.sleep") as sleep:
            self.assertFalse(wait_for_batch_checkpoint(store, 95496, url, max_reads=3))
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([x["id"] for x in store.current["pending"]], [95496])
        self.assertIsNone(store.current["in_flight"])

    def test_checkpoint_visibility_rejects_different_published_url(self):
        s = state()
        ingest(s, [deal(95496)])
        s["pending"] = []
        s["published"]["95496"] = {"url": "https://blog.naver.com/mw5114/224472999999",
                                   "at": datetime.now(timezone.utc).isoformat()}
        store = ConflictStore(initial=s, conflicts=0)
        with self.assertRaisesRegex(RuntimeError, "NAVER_BATCH_RECEIPT_CONFLICT_STOP"):
            wait_for_batch_checkpoint(
                store, 95496, "https://blog.naver.com/mw5114/224472901234")

    def test_batch_defers_stale_read_without_second_publish_or_red_failure(self):
        s = state()
        ingest(s, [deal(95496), deal(95497)])
        original = deepcopy(s)
        store = ConflictStore(initial=s, conflicts=0)
        called = []
        def confirmed_publish(current, *, with_receipt=False):
            row, _ = current.read()
            mid = row["pending"][0]["id"]
            url = "https://blog.naver.com/mw5114/224472901234"
            claim_before_publish(current, mid)
            checkpoint_published(current, mid, url)
            called.append(mid)
            return {"source_id": mid, "url": url}
        def never_visible(current, source_id, url):
            self.assertEqual(source_id, 95496)
            self.assertEqual(url, "https://blog.naver.com/mw5114/224472901234")
            return False
        with patch("gh_only.naver_blog_jirum.publish_one",
                   side_effect=confirmed_publish) as post, patch(
                "gh_only.naver_blog_jirum.wait_for_batch_checkpoint",
                side_effect=never_visible):
            self.assertEqual(publish_batch(store), 0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(called, [95496])
        self.assertIn("95496", store.current["published"])
        self.assertEqual([x["id"] for x in store.current["pending"]], [95497])

    def test_batch_missing_return_receipt_is_fail_closed(self):
        s = state()
        ingest(s, [deal(95496), deal(95497)])
        store = ConflictStore(initial=s, conflicts=0)
        with patch("gh_only.naver_blog_jirum.publish_one", return_value=None) as post:
            with self.assertRaisesRegex(
                    RuntimeError, "NAVER_BATCH_POST_RETURNED_NO_COMMITTED_RECEIPT"):
                publish_batch(store)
        self.assertEqual(post.call_count, 1)

    def test_batch_limit_is_bounded(self):
        store = ConflictStore(initial=state(), conflicts=0)
        for invalid in (0, 7, -1, True, "4"):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "NAVER_BATCH_LIMIT_INVALID"):
                    publish_batch(store, max_posts=invalid)

    def test_batch_expiry_handles_confirmed_409(self):
        s = state()
        now = datetime.now(timezone.utc)
        ingest(s, [deal(95496, minutes=65)],
               reference=now - timedelta(minutes=10))
        store = ConflictStore(initial=s, conflicts=1)
        with patch("gh_only.naver_blog_jirum.time.sleep"):
            self.assertEqual(expire_unclaimed_before_next(store), 1)
        self.assertEqual(store.current["pending"], [])
        self.assertEqual(store.current["skipped"]["95496"]["reason"],
                         "STALE_OR_UNKNOWN_TIMESTAMP")
        self.assertIn("95490", store.current["published"])

    def test_known_409_scan_rebases_and_preserves_other_state(self):
        store = ConflictStore()
        with patch("gh_only.naver_blog_jirum.old.collect_since",
                   return_value=[deal(95496)]), patch(
                   "gh_only.naver_blog_jirum.time.sleep"):
            result = scan(store)
        self.assertEqual(result["new"], 1)
        self.assertEqual(store.write_count, 2)
        self.assertEqual(store.current["pending"][0]["id"], 95496)
        self.assertIn("95490", store.current["published"])

    def test_known_409_before_claim_rebases_without_post(self):
        s = state()
        ingest(s, [deal(95496)])
        store = ConflictStore(initial=s, conflicts=1)
        with patch("gh_only.naver_blog_jirum.time.sleep"):
            claim_before_publish(store, 95496)
        self.assertEqual(store.write_count, 2)
        self.assertEqual(store.current["in_flight"]["id"], 95496)
        self.assertIn("95490", store.current["published"])

    def test_known_409_post_receipt_retries_only_state_commit(self):
        s = state()
        ingest(s, [deal(95496)])
        s["in_flight"] = {"id":95496, "status":"REVIEW_REQUIRED",
                          "run_id":"42738963892",
                          "at":datetime.now(timezone.utc).isoformat()}
        store = ConflictStore(initial=s, conflicts=1)
        link = "https://blog.naver.com/mw5114/224471000000"
        with patch("gh_only.naver_blog_jirum.time.sleep"):
            checkpoint_published(store, 95496, link)
        self.assertIsNone(store.current["in_flight"])
        self.assertEqual(store.current["pending"], [])
        self.assertEqual(store.current["published"]["95496"]["url"], link)
        self.assertIn("95490", store.current["published"])
        self.assertEqual(store.write_count, 2)

    def test_409_retry_is_bounded_and_does_not_touch_live_publish(self):
        store = ConflictStore(conflicts=10)
        with patch("gh_only.naver_blog_jirum.old.collect_since",
                   return_value=[deal(95496)]), patch(
                   "gh_only.naver_blog_jirum.time.sleep"):
            with self.assertRaises(NaverStateConflict):
                scan(store)
        self.assertEqual(store.write_count, 5)

    def test_ambiguous_write_error_is_never_retried(self):
        class UncertainStore(ConflictStore):
            def write(self, next_state, sha, message):
                self.write_count += 1
                raise RuntimeError("NAVER_STATE_COMMIT_UNCERTAIN")
        store = UncertainStore()
        with patch("gh_only.naver_blog_jirum.old.collect_since",
                   return_value=[deal(95496)]), patch(
                   "gh_only.naver_blog_jirum.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "COMMIT_UNCERTAIN"):
                scan(store)
        self.assertEqual(store.write_count, 1)

    def test_long_title_is_safely_shortened_and_keeps_discount(self):
        title = "[쿠팡🔥핫딜] " + ("테팔 인덕션 매직핸즈 스테인리스 프라이팬 22cm + " * 6) + "[30%할인]"
        actual = naver_title(title)
        self.assertLessEqual(len(actual.encode("utf-16-le")) // 2, NAVER_TITLE_MAX_UTF16_UNITS)
        self.assertTrue(actual.startswith("[쿠팡🔥핫딜] "))
        self.assertTrue(actual.endswith("[30%할인]"))
        self.assertIn("…", actual)
        self.assertEqual(naver_title("[쿠팡🔥핫딜] 짧은 상품 [25%할인]"),
                         "[쿠팡🔥핫딜] 짧은 상품 [25%할인]")

    def test_long_title_with_emoji_stays_within_utf16_limit(self):
        original = "[쿠팡🔥핫딜] " + "📱🥘" * 55 + " [20%할인]"
        actual = naver_title(original)
        self.assertLessEqual(len(actual.encode("utf-16-le")) // 2, NAVER_TITLE_MAX_UTF16_UNITS)
        self.assertTrue(actual.endswith(" [20%할인]"))

    def test_abandoned_claim_quarantine_never_replays(self):
        s = state()
        row = deal(95496, minutes=90)
        ingest(s, [row], reference=datetime.now(timezone.utc) - timedelta(minutes=85))
        s["in_flight"] = {
            "id":95496, "status":"REVIEW_REQUIRED",
            "run_id":"42712155025",
            "at": (datetime.now(timezone.utc) - timedelta(minutes=80)).isoformat()
        }
        validate_state(s)
        self.assertTrue(archive_stale_claim(s))
        self.assertIsNone(s["in_flight"])
        self.assertEqual(s["pending"], [])
        self.assertEqual(s["skipped"]["95496"]["reason"], "PUBLISH_OUTCOME_UNCONFIRMED_NO_AUTORETRY")
        self.assertEqual(s["skipped"]["95496"]["claimed_run_id"], "42712155025")

    def test_recent_claim_remains_locked(self):
        s = state()
        ingest(s, [deal(95496)])
        s["in_flight"] = {
            "id":95496, "status":"REVIEW_REQUIRED",
            "run_id":"999",
            "at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        }
        self.assertFalse(archive_stale_claim(s))
        self.assertEqual(s["pending"][0]["id"], 95496)

    def test_new_fresh_deal_enqueued_once(self):
        s = state()
        n, expired = ingest(s, [deal(95497), deal(95496)])
        self.assertEqual((n, expired), (2, 0))
        self.assertEqual([x["id"] for x in s["pending"]], [95496, 95497])
        self.assertEqual(s["last_scanned_id"], 95497)
        again, _ = ingest(s, [deal(95497)])
        self.assertEqual(again, 0)
        self.assertEqual(len(s["pending"]), 2)

    def test_no_stale_or_unknown_timestamp(self):
        s = state()
        stale = deal(95496, minutes=120)
        missing = deal(95497)
        missing["posted_at_utc"] = None
        n, _ = ingest(s, [stale, missing])
        self.assertEqual(n, 0)
        self.assertEqual(s["pending"], [])
        self.assertEqual(len(s["skipped"]), 2)

    def test_price_missing_and_non_coupang_suppressed(self):
        s = state()
        n, _ = ingest(s, [deal(95496, price=None), deal(95497, kind="other")])
        self.assertEqual(n, 0)
        self.assertFalse(s["pending"])
        self.assertEqual(s["skipped"]["95496"]["reason"], "PRICE_NOT_FOUND")

    def test_prior_watermark_never_backfilled(self):
        s = state()
        n, _ = ingest(s, [deal(95493), deal(95494), deal(95495)])
        self.assertEqual(n, 0)
        self.assertEqual(s["last_scanned_id"], 95495)

    def test_claim_must_reference_pending(self):
        s = state()
        s["in_flight"] = {"id": 95496, "status": "REVIEW_REQUIRED"}
        with self.assertRaisesRegex(ValueError, "NAVER_FIFO_BAD_CLAIM"):
            validate_state(s)

    def test_timestamp_not_inferred(self):
        self.assertFalse(source_fresh({"posted_at_utc": None}))

    def test_naverblog_requires_api_issued_link_with_no_subid_receipt(self):
        good = "https://link.coupang.com/a/AbcDEf1234"
        receipt = {
            "issued_by": "COUPANG_PARTNERS_API", "status": "LINK_READY",
            "partners_link": good, "subid_requested": None,
        }
        self.assertIsNone(validate_naver_link_subid(good, receipt))
        self.assertIsNone(validate_naver_link_subid(
            "https://coupa.ng/AbcDEf1234",
            {**receipt, "partners_link": "https://coupa.ng/AbcDEf1234"}))
        old = ("https://link.coupang.com/re/AFFSDP?"
               "lptag=AF9668915&subid=naverblog&pageKey=123&itemId=456")
        for link, evidence in (
            (old, receipt), (good, None), (good, {**receipt, "subid_requested": "naverblog"}),
            (good, {**receipt, "subid_requested": "cafe"}),
            (good, {k:v for k,v in receipt.items() if k != "subid_requested"}),
            (good, {**receipt, "partners_link": "https://link.coupang.com/a/OtherLink999"}),
            (good, {**receipt, "issued_by": "HAND_COMPOSED"}),
            (good, {**receipt, "status": "NOT_READY"}),
        ):
            with self.subTest(link=link, evidence=evidence):
                with self.assertRaisesRegex(ValueError, "NAVER_BLOG_OFFICIAL_LINK_OR_SUBID_INVALID"):
                    validate_naver_link_subid(link, evidence)

    def test_official_link_replaces_unissued_candidate_exactly_once(self):
        old = ("https://link.coupang.com/re/AFFSDP?"
               "lptag=AF9668915&subid=naverblog&pageKey=12345678&itemId=45678901")
        identity = {"status": "EXACT_ITEM_IDENTIFIED",
                    "product_id": "12345678", "item_id": "45678901",
                    "vendor_item_id": "90123456"}
        draft = {"product_identity": identity, "composed_affiliate_url": old,
                 "caption": {"caption": "🛒 쿠팡에서 상품정보 더보기 : " + old},
                 "affsdp": {"status": "UNISSUED_AFFSDP_CANDIDATE"}}
        issued = "https://link.coupang.com/a/AbcDEf1234"
        with patch("gh_only.coupang_partners_api.create_product_deeplink",
                   return_value={"partners_link": issued, "product_id": "12345678",
                                 "item_id": "45678901", "vendor_item_id": "90123456",
                                 "subid_requested": None}) as called:
            result = apply_official_api_link(draft, 95496)
        called.assert_called_once_with(identity)
        self.assertEqual(result["composed_affiliate_url"], issued)
        self.assertEqual(result["caption"]["caption"].count(issued), 1)
        self.assertNotIn("AFFSDP", result["caption"]["caption"])
        self.assertEqual(result["official_link_receipt"]["telegram_message_id"], 95496)
        self.assertFalse(result["affsdp"]["commission_attribution_verified"])
        self.assertTrue(result["affsdp"]["official_link_issued"])
        self.assertIsNone(validate_naver_link_subid(
            issued, result["official_link_receipt"]))

    def test_official_link_failure_never_uses_unissued_url(self):
        from gh_only.coupang_partners_api import CoupangPartnersError
        old = "https://link.coupang.com/re/AFFSDP?lptag=AF9668915"
        draft = {"product_identity": {
                    "status": "EXACT_ITEM_IDENTIFIED", "product_id": "12345678",
                    "item_id": "45678901", "vendor_item_id": None},
                 "composed_affiliate_url": old,
                 "caption": {"caption": "🛒 쿠팡에서 상품정보 더보기 : " + old}}
        with patch("gh_only.coupang_partners_api.create_product_deeplink",
                   side_effect=CoupangPartnersError("COUPANG_API_HTTP_403")):
            with self.assertRaisesRegex(CoupangPartnersError, "COUPANG_API_HTTP_403"):
                apply_official_api_link(draft, 95496)
        self.assertEqual(draft["composed_affiliate_url"], old)
        self.assertNotIn("official_link_receipt", draft)
        with self.assertRaises(ValueError):
            validate_naver_link_subid(old)

    def test_separate_state_schema(self):
        s = state()
        s["schema"] = "daangn-jirum-manual-fifo-v1"
        with self.assertRaisesRegex(ValueError, "NAVER_FIFO_INVALID_SCHEMA"):
            validate_state(s)


if __name__ == "__main__":
    unittest.main()
