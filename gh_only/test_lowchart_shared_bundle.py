"""No-post tests for real Lowchart media bytes across isolated GitHub runners."""
from __future__ import annotations

import copy
from hashlib import sha1, sha256
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from gh_only.lowchart_shared_bundle import (
    SCHEMA, _manifest_valid, _photo_quality, local_verified_chart,
    publish_bundle, recover_bundle, _file_meta, _git_blob_read,
)
from gh_only.test_lowchart_chart_capture import ChartUnitTests
from gh_only.test_default_deal_automation import (
    DEAL, PID, IID, IDENTITY, H_TITLE, HIST_HTML,
)
from gh_only.lowchart_price_reference import build_url, parse_reference
from gh_only.default_deal_automation import enrich_deal

SOURCE_ID = DEAL["source_message_id"]
TITLE = H_TITLE.split(" - 31,340원")[0]
CDN = "https://thumbnail.coupangcdn.com/test/exact-product.jpg"


def photo_bytes():
    im = Image.new("RGB", (360, 360), "white")
    d = ImageDraw.Draw(im)
    for n in range(5, 355, 9):
        d.line([(n, 0), (360-n, 360)], fill=(n//2, 50, 130), width=3)
    buf = BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def chart_bytes():
    return ChartUnitTests.synthetic_native_png(pale=False)


def exact_history():
    result = parse_reference(
        HIST_HTML, build_url(PID, IID), PID, IID,
        ("휘카레", "230g", "20개"), 12050,
    )
    return {"http_status":200, **result}


class LowchartVerifiedBundleTests(unittest.TestCase):
    def test_producer_consumer_reuses_original_photo_chart_title_mean(self):
        data = {}
        manifests = {}
        def writer(content, token):
            key = sha1(b"blob %d\0" % len(content) + content).hexdigest()
            data[key] = bytes(content)
            return key
        def reader(meta, token):
            payload = data.get(meta["git_blob_sha"])
            if not payload or sha256(payload).hexdigest() != meta["sha256"]:
                return None
            return payload
        def record_store(sid, obj, token):
            manifests[sid] = copy.deepcopy(obj)
            return True
        with TemporaryDirectory() as td:
            folder = Path(td)
            orig = folder / f"jirum_{SOURCE_ID}_lowchart_photo.jpg"
            chart = folder / f"jirum_{SOURCE_ID}_lowchart_chart.png"
            orig.write_bytes(photo_bytes())
            chart.write_bytes(chart_bytes())
            self.assertTrue(publish_bundle(
                SOURCE_ID, IDENTITY, exact_history(), TITLE, CDN,
                orig, chart, token="fake",
                blob_writer=writer, manifest_writer=record_store))
            manifest = manifests[SOURCE_ID]
            self.assertEqual(manifest["schema"], SCHEMA)
            self.assertTrue(_manifest_valid(manifest, SOURCE_ID, PID, IID))
            self.assertEqual(manifest["title"], TITLE)
            self.assertNotIn("affiliate", str(manifest))
            with TemporaryDirectory() as dest:
                bundle = recover_bundle(
                    SOURCE_ID, IDENTITY, Path(dest),
                    token="fake", reader=lambda sid,t: manifests.get(sid),
                    blob_reader=reader, sleeper=lambda _: None)
                self.assertIsNotNone(bundle)
                self.assertEqual(bundle["title"], TITLE)
                self.assertEqual(bundle["history"]["observed_average_krw"],34575)
                self.assertEqual(bundle["photo_path"].read_bytes(), orig.read_bytes())
                self.assertEqual(bundle["chart_path"].read_bytes(), chart.read_bytes())
                self.assertEqual(local_verified_chart(
                    {"verified_exact_item": True,
                     "product_id": PID, "item_id": IID,
                     "source_url": build_url(PID, IID)},
                    Path(dest), SOURCE_ID), bundle["chart_path"])
                self.assertIsNone(local_verified_chart(
                    {"verified_exact_item": True,
                     "product_id": PID, "item_id": "0009998888",
                     "source_url": build_url(PID, IID)},
                    Path(dest), SOURCE_ID))

    def test_ready_cache_miss_does_not_sleep(self):
        calls = []
        with TemporaryDirectory() as td:
            result = recover_bundle(
                SOURCE_ID, IDENTITY, Path(td), token="fake", wait=False,
                reader=lambda sid, token: calls.append(sid) or None,
                sleeper=lambda delay: self.fail("cache probe must not sleep"))
        self.assertIsNone(result)
        self.assertEqual(calls, [SOURCE_ID])

    def test_ready_cache_skips_external_collection(self):
        for delayed in (False, True):
            with self.subTest(delayed=delayed), TemporaryDirectory() as td:
                folder = Path(td)
                photo = folder / f"jirum_{SOURCE_ID}_lowchart_photo.jpg"
                chart = folder / f"jirum_{SOURCE_ID}_lowchart_chart.png"
                photo.write_bytes(photo_bytes())
                chart.write_bytes(chart_bytes())
                cached = {
                    "title": TITLE, "image_url": CDN, "photo_path": photo,
                    "chart_path": chart, "history": exact_history(),
                }
                with patch.dict(os.environ, {
                    "JIRUM_LOWCHART_BUNDLE_ENABLED": "true",
                    "JIRUM_LOWCHART_CACHE_FIRST_ENABLED": "true",
                    "GH_STATE_TOKEN": "fake",
                }), patch("gh_only.lowchart_shared_bundle.recover_bundle",
                          side_effect=[None, cached] if delayed else [cached]) as load, \
                     patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value={"http_status": 403,
                                         "status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR"}) as lookup, \
                     patch("gh_only.lowchart_price_reference._fetch_one_once") as price, \
                     patch("gh_only.default_deal_automation.download_exact_photo") as download, \
                     patch("gh_only.lowchart_shared_reference.maybe_share_or_recover") as share, \
                     patch("gh_only.lowchart_shared_bundle.prepare_native_chart") as capture, \
                     patch("gh_only.lowchart_shared_bundle.publish_bundle") as publish:
                    row = enrich_deal(DEAL, folder, resolver=lambda _: IDENTITY,
                                      owner_tag="AF9668915")
                self.assertEqual(lookup.call_count, int(delayed))
                self.assertEqual(load.call_count, 2 if delayed else 1)
                self.assertTrue(all(c.kwargs.get("wait") is False for c in load.call_args_list))
                for mock in (price, download, share, capture, publish):
                    mock.assert_not_called()
                self.assertEqual(row["caption"]["observed_average_krw"], 34575)
                self.assertEqual(row["photo"]["saved_name"], photo.name)
                self.assertTrue(row["image_exact_item_page_verified"])
                self.assertTrue(row["history"]["shared_from_verified_sibling"])

    def test_403_retry_interrupts_without_another_provider_request(self):
        from gh_only.lowchart_product_photo import lookup_exact_item_photo
        from gh_only.lowchart_price_reference import fetch_one
        for photo_mode in (True, False):
            with self.subTest(photo_mode=photo_mode):
                fn = lookup_exact_item_photo if photo_mode else fetch_one
                args = (IDENTITY, TITLE) if photo_mode else (PID, IID, (), 12050)
                path = ("gh_only.lowchart_product_photo._lookup_exact_item_photo_once" if photo_mode
                        else "gh_only.lowchart_price_reference._fetch_one_once")
                delays = []
                result = {"status": "shared", "http_status": 200}
                with patch.dict(os.environ, {"JIRUM_LOWCHART_403_RETRY_ENABLED": "true"}), \
                     patch(path, return_value={"http_status": 403}) as once:
                    actual = fn(*args, sleeper=delays.append, ready=lambda: result if delays else None)
                self.assertEqual(actual, result)
                self.assertEqual(once.call_count, 1)
                self.assertEqual(delays, [2])

    def test_unlisted_200_requires_visible_missing_product_marker(self):
        from gh_only.lowchart_product_photo import lookup_exact_item_photo
        from gh_only.test_lowchart_product_photo import MockResponse
        for raw, expected in (
            ("<html><body>등록되지 않은 상품입니다.</body></html>", True),
            ("<html><script>var s='등록되지 않은 상품입니다';</script></html>", False),
            ("<html><body>잠시 후 다시 이용해주세요.</body></html>", False),
            ("<html><body>Access Denied</body></html>", False),
        ):
            out = lookup_exact_item_photo(IDENTITY, TITLE,
                opener=lambda req, timeout: MockResponse(req.full_url, raw.encode()))
            self.assertEqual(out["http_status"], 200)
            self.assertEqual(out["status"] == "LOWCHART_CONFIRMED_UNLISTED_200", expected)

    def test_naver_api_fallback_selects_matching_options_and_validates_draft(self):
        from gh_only.naver_api_image_photo import fetch_matched_photo, matching_title
        from gh_only.test_lowchart_product_photo import MockResponse
        from gh_only.jirum_manual_queue import validate_draft, permanent_lowchart_failure
        import json
        self.assertTrue(matching_title("닌자 Foodi 파워 프로세서 블렌더 CB350KR",
                         "닌자 CB350KR 스마트 파워 업 초고속 프로세서 블렌더 믹서기 | 텐바이텐"))
        self.assertFalse(matching_title("에낙 라면과자 30g 48개", "에낙 라면과자 30g 24개 - 쿠팡"))
        self.assertFalse(matching_title("닌자 블렌더 CB350KR", "닌자 블렌더 CB350KR 교체 칼날"))
        url = "https://shop-phinf.pstatic.net/test/valid.jpg"
        requests = []
        def op(req, timeout):
            requests.append(req)
            if "naverapihub" in req.full_url:
                data = {"items": [
                    {"title": TITLE.replace("20개", "10개"), "link": url},
                    {"title": TITLE, "link": url},
                ]}
                return MockResponse(req.full_url, json.dumps(data).encode(), "application/json")
            self.assertEqual(req.full_url, url)
            self.assertFalse(any("key" in k.lower() for k in req.headers))
            return MockResponse(url, photo_bytes(), "image/jpeg")
        with TemporaryDirectory() as td:
            folder = Path(td)
            with patch.dict(os.environ, {
                "JIRUM_IMAGE_SOURCE": "lowchart",
                "JIRUM_LOWCHART_BUNDLE_ENABLED": "true",
                "JIRUM_LOWCHART_CACHE_FIRST_ENABLED": "true",
                "JIRUM_NAVER_API_UNLISTED_FALLBACK": "true",
                "NAVER_API_HUB_KEY_ID": "offline-test",
                "NAVER_API_HUB_KEY": "offline-test",
                "COUPANG_PARTNER_LPTAG": "AF9668915",
                "COUPANG_PARTNER_SUBID": "",
            }), patch("gh_only.lowchart_shared_bundle.recover_bundle", return_value=None), \
                patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                    return_value={"http_status": 200, "status": "LOWCHART_CONFIRMED_UNLISTED_200"}), \
                patch("gh_only.lowchart_price_reference._fetch_one_once") as price, \
                patch("gh_only.naver_api_image_photo.fetch_matched_photo",
                      side_effect=lambda *a: fetch_matched_photo(*a, opener=op)) as api:
                deal = {**DEAL, "source_text": TITLE + "\n12,050원", "title": TITLE}
                row = enrich_deal(deal, folder, resolver=lambda _: IDENTITY,
                                  owner_tag="AF9668915",
                                  fallcent_high_lookup=lambda *a: {"status": "NO_PRICE"},
                                  fallcent_similar_current_lookup=lambda *a: {"status": "NO_PRICE"})
                price.assert_not_called()
                api.assert_called_once()
                self.assertEqual(len(requests), 2)
                self.assertEqual(row["image_source"], "naver_api_matched_image")
                self.assertFalse(row["image_exact_item_page_verified"])
                self.assertIsNone(row["caption"]["observed_average_krw"])
                (folder / "drafts.json").write_text(json.dumps({
                    "schema": "github-jirum-default-price-photo-affsdp-draft-v2", "drafts": [row]
                }), encoding="utf-8")
                self.assertIsNone(permanent_lowchart_failure(folder))
                verified = validate_draft(folder, SOURCE_ID)
                self.assertIsNone(verified["chart_identity"])
                self.assertIn("네이버 이미지 검색 대표 사진", verified["body"])
                row["image_http_status"] = 403
                (folder / "drafts.json").write_text(json.dumps({
                    "schema": "github-jirum-default-price-photo-affsdp-draft-v2", "drafts": [row]
                }), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    validate_draft(folder, SOURCE_ID)

    def test_local_lowchart_200_publishes_both_original_assets_before_post(self):
        og = {
            "status": "LOWCHART_EXACT_ITEM_IMAGE",
            "og_title": TITLE,
            "og_url": build_url(PID, IID),
            "og_image": CDN,
            "lowchart_title_verified": True,
            "image_source": "lowchart_exact_item_public_page",
            "_verified_public_item_html": HIST_HTML,
        }
        submitted = []
        def download(url, folder, mid):
            self.assertEqual(url, CDN)
            name = f"jirum_{mid}_lowchart_photo.jpg"
            (folder/name).write_bytes(photo_bytes())
            return {
                "status": "IMAGE_SAVED", "saved_name": name,
                "image_source": "lowchart_exact_item_public_page",
            }
        def capture(identity, folder, sid):
            self.assertEqual(identity["item_id"], IID)
            file = folder/f"jirum_{sid}_lowchart_chart.png"
            file.write_bytes(chart_bytes())
            return file
        with TemporaryDirectory() as td, \
             patch.dict(os.environ, {"JIRUM_IMAGE_SOURCE": "lowchart",
                                     "JIRUM_LOWCHART_BUNDLE_ENABLED": "true",
                                     "GH_STATE_TOKEN": "fake"}), \
             patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value=og), \
             patch("gh_only.lowchart_shared_reference.maybe_share_or_recover",
                   return_value=None), \
             patch("gh_only.lowchart_shared_bundle.prepare_native_chart",
                   side_effect=capture) as chart, \
             patch("gh_only.lowchart_shared_bundle.publish_bundle",
                   side_effect=lambda *a, **k:
                   submitted.append(a) or True) as share:
            row = enrich_deal(
                DEAL, Path(td), resolver=lambda _: IDENTITY,
                photo_downloader=download, owner_tag="AF9668915",
            )
        chart.assert_called_once()
        share.assert_called_once()
        self.assertEqual(submitted[0][0], SOURCE_ID)
        self.assertEqual(submitted[0][3], TITLE)
        self.assertEqual(submitted[0][4], CDN)
        self.assertEqual(submitted[0][5].suffix, ".jpg")
        self.assertEqual(submitted[0][6].suffix, ".png")
        self.assertEqual(row["caption"]["observed_average_krw"], 34575)
        self.assertEqual(row["image_source"],"lowchart_exact_item_public_page")

    def test_wrong_sku_wrong_source_or_old_snapshot_refused(self):
        captured = {}
        with TemporaryDirectory() as td:
            photo = Path(td) / "p.jpg"
            photo.write_bytes(photo_bytes())
            with patch("gh_only.lowchart_shared_bundle._git_blob_write",
                       return_value="a"*40), \
                 patch("gh_only.lowchart_shared_bundle._manifest_write",
                       side_effect=lambda sid,obj,token:
                       captured.update(record=obj) or True):
                self.assertTrue(publish_bundle(
                    SOURCE_ID, IDENTITY, exact_history(), TITLE, CDN,
                    photo, token="fake"))
            record = captured["record"]
            self.assertFalse(_manifest_valid(record, SOURCE_ID+1, PID, IID))
            self.assertFalse(_manifest_valid(record, SOURCE_ID, PID, "7777777777"))
            self.assertFalse(_manifest_valid(record, SOURCE_ID, "7777777777", IID))
            bad = copy.deepcopy(record)
            bad["title"] = "역대급최저가"
            self.assertFalse(_manifest_valid(bad, SOURCE_ID, PID, IID))
            bad = copy.deepcopy(record)
            bad["price_record"]["history"]["observed_average_krw"] = 1
            self.assertFalse(_manifest_valid(bad, SOURCE_ID, PID, IID))
            bad = copy.deepcopy(record)
            bad["image_url"] = "https://evil.example/photo.jpg"
            self.assertFalse(_manifest_valid(bad, SOURCE_ID, PID, IID))
            bad = copy.deepcopy(record)
            bad["price_record"]["observed_at_utc"] = "2020-01-01T00:00:00+00:00"
            self.assertFalse(_manifest_valid(bad, SOURCE_ID, PID, IID))

    def test_invalid_photo_or_chart_bytes_never_become_exact_item(self):
        self.assertTrue(_photo_quality(photo_bytes(), ".jpg"))
        self.assertFalse(_photo_quality(b"\xff\xd8\xff" + b"x"*1200, ".jpg"))
        self.assertFalse(_photo_quality(chart_bytes(), ".jpg"))
        with TemporaryDirectory() as td:
            corrupt = Path(td)/"p.jpg"
            corrupt.write_bytes(b"junk"*400)
            self.assertFalse(publish_bundle(
                SOURCE_ID, IDENTITY, exact_history(), TITLE, CDN,
                corrupt, token="fake", blob_writer=lambda *a:"a"*40,
                manifest_writer=lambda *a:self.fail("invalid photo stored")))
            self.assertFalse(publish_bundle(
                SOURCE_ID, IDENTITY,
                {**exact_history(), "http_status":403}, TITLE, CDN,
                corrupt, token="fake", blob_writer=lambda *a:"a"*40,
                manifest_writer=lambda *a:self.fail("invalid history stored")))

    def test_403_consumer_uses_sibling_title_photo_price_not_first_search_result(self):
        from urllib.error import HTTPError
        with TemporaryDirectory() as td:
            folder = Path(td)
            recovered_photo = folder/f"jirum_{SOURCE_ID}_lowchart_photo.jpg"
            recovered_photo.write_bytes(photo_bytes())
            recovered_chart = folder/f"jirum_{SOURCE_ID}_lowchart_chart.png"
            recovered_chart.write_bytes(chart_bytes())
            # Real incident type: Telegram title is a meaningless hype phrase.
            deal = {**DEAL, "title": "역대급최저가",
                    "source_text": "역대급최저가\n6,310원\n"
                                   "https://link.coupang.com/a/example"}
            sibling = {
                "title":TITLE, "image_url":CDN,
                "photo_path":recovered_photo,
                "chart_path":recovered_chart,
                "history":exact_history(),
            }
            with patch.dict(os.environ, {
                "JIRUM_LOWCHART_BUNDLE_ENABLED":"true",
                "GH_STATE_TOKEN":"fake",
            }), \
                 patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                       return_value={
                           "status":"LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                           "http_status":403,"og_image":None}), \
                 patch("gh_only.lowchart_price_reference._fetch_one_once",
                       return_value={"http_status":403,"reason":"HTTP_ERROR"}), \
                 patch("gh_only.lowchart_shared_reference.maybe_share_or_recover",
                       return_value=None), \
                 patch("gh_only.lowchart_shared_bundle.recover_bundle",
                       return_value=sibling) as load, \
                 patch("gh_only.fallcent_search_photo.fetch_fallcent_product_photo") as fallcent, \
                 patch("gh_only.naver_web_image_photo.fetch_first_naver_photo") as first:
                row = enrich_deal(
                    deal, folder, resolver=lambda _: IDENTITY,
                    owner_tag="AF9668915")
            load.assert_called_once()
            fallcent.assert_not_called()
            first.assert_not_called()
            self.assertEqual(row["caption"]["product_name"], TITLE)
            self.assertEqual(row["caption"]["observed_average_krw"], 34575)
            self.assertEqual(row["photo"]["saved_name"],recovered_photo.name)
            self.assertEqual(row["image_source"],"lowchart_exact_item_public_page")
            self.assertTrue(row["image_exact_item_page_verified"])
            self.assertEqual(row["comparison_price_source"],"lowchart_public_history")
            self.assertTrue(row["history"]["shared_from_verified_sibling"])
            # Both channel publishers use the SAME draft validator, so the
            # recovered product title/photo must pass without fake evidence.
            from gh_only.jirum_manual_queue import validate_draft
            import json
            (folder / "drafts.json").write_text(
                json.dumps({
                    "schema":"github-jirum-default-price-photo-affsdp-draft-v2",
                    "drafts":[row],
                }, ensure_ascii=False), encoding="utf-8",
            )
            # The production workflows use this same exact-item mode.
            with patch.dict(os.environ, {
                "JIRUM_IMAGE_SOURCE":"lowchart",
                "COUPANG_PARTNER_LPTAG":"AF9668915",
                "COUPANG_PARTNER_SUBID":"",
            }):
                verified = validate_draft(folder, SOURCE_ID)
            self.assertEqual(verified["source_id"], SOURCE_ID)
            self.assertEqual(verified["image"], recovered_photo)
            self.assertEqual(
                (verified["chart_identity"]["product_id"],
                 verified["chart_identity"]["item_id"]), (PID, IID))

    def test_both_403_unavailable_sibling_never_uses_unverified_search_photos(self):
        with TemporaryDirectory() as td, \
             patch.dict(os.environ, {"JIRUM_LOWCHART_BUNDLE_ENABLED":"true",
                                     "GH_STATE_TOKEN":"fake"}), \
             patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value={"status":"LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                                 "http_status":403}), \
             patch("gh_only.lowchart_price_reference._fetch_one_once",
                   return_value={"http_status":403}), \
             patch("gh_only.lowchart_shared_reference.maybe_share_or_recover",
                   return_value=None), \
             patch("gh_only.lowchart_shared_bundle.recover_bundle",
                   return_value=None), \
             patch("gh_only.fallcent_search_photo.fetch_fallcent_product_photo") as fallcent, \
             patch("gh_only.naver_web_image_photo.fetch_first_naver_photo") as first:
            row = enrich_deal(
                DEAL, Path(td), resolver=lambda _: IDENTITY,
                owner_tag="AF9668915",
                fallcent_similar_current_lookup=lambda *a: {"status":"NO_PRICE"})
        fallcent.assert_not_called()
        first.assert_not_called()
        self.assertEqual(row["photo"]["status"],"PHOTO_EVIDENCE_UNAVAILABLE")
        self.assertFalse(row["image_exact_item_page_verified"])
        self.assertFalse(row["representative_naver_image"])
        self.assertFalse(row["representative_fallcent_image"])


if __name__ == "__main__":
    unittest.main()
