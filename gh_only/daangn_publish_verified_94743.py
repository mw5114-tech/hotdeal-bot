"""Publish exactly one already-inspected Jirum source item to owner's Daangn Cafe.
Only for an explicitly requested, first-attempt GitHub workflow run.
No retry after clicking, even if the result is uncertain.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import (
    parse_daangn_storage_state, read_one_draft, prepare_browser_editor, DISCLOSURE,
)

CAFE = "https://cafe.daangn.com/i-gagyeog-mos-c"
FOLDER = Path("one-post-preview")
RESULT = Path("one-post-test-result")
SOURCE_ID = 94743
SALE = 524500
REFERENCE_AVG = 692362
DISCOUNT = 25
BRANCH = "refs/heads/test/daangn-publish-next-94743-20261101"

def normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "")

def publish_once() -> dict:
    out = {"status": "NOT_STARTED", "source_id": SOURCE_ID,
           "publish_click_attempted": False, "post_published_verified": False,
           "post_url": None, "board": "오늘의 핫딜"}
    if (os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
        or os.environ.get("GITHUB_REF") != BRANCH
        or os.environ.get("ALLOW_ONE_TEST_POST") != "true"):
        return {**out, "status": "ONE_SHOT_GUARD_NOT_MET"}
    try:
        report = json.loads((FOLDER / "drafts.json").read_text("utf-8"))
        rows = report.get("drafts") or []
        if len(rows) != 1 or rows[0].get("source_message_id") != SOURCE_ID:
            raise ValueError("UNEXPECTED_SOURCE_ITEM")
        row = rows[0]
        if row.get("caption", {}).get("sale_price_krw") != SALE:
            raise ValueError("PRICE_CHANGED")
        if row.get("caption", {}).get("observed_average_krw") != REFERENCE_AVG:
            raise ValueError("HISTORY_REFERENCE_CHANGED")
        if row.get("caption", {}).get("approx_discount_vs_historical_mean_pct") != DISCOUNT:
            raise ValueError("PERCENT_CHANGED")
        article = read_one_draft(FOLDER)
        if (article["source_id"] != SOURCE_ID or not article["image"]
            or not article["image"].is_file()
            or not article["title"].endswith("[25%할인]")
            or "📦 상품명 :" not in article["body"]
            or "524,500원" not in article["body"]
            or "692,362원" not in article["body"]
            or "※ 실시간 핫딜 특성상" not in article["body"]
            or "카페 가입 알림 설정하고 꿀템 챙겨가세요." not in article["body"]
            or not article["body"].rstrip().endswith(DISCLOSURE)
            or "AF9668915" not in article["candidate_url"]):
            raise ValueError("ARTICLE_FORMAT_NOT_VERIFIED")
        out["title"] = article["title"]
        out["claim_historical_low_verified"] = row.get("observed_low_verified_for_deal") is True
        state = parse_daangn_storage_state(os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON", ""))
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(storage_state=state, locale="ko-KR",
                                          viewport={"width": 1366, "height": 950})
                page = ctx.new_page()
                response = page.goto(CAFE, wait_until="domcontentloaded", timeout=28000)
                if not response or response.status != 200 or urlsplit(page.url).hostname != "cafe.daangn.com":
                    raise RuntimeError("CAFE_PAGE_OR_SESSION_UNAVAILABLE")
                page.wait_for_timeout(1400)
                fields = prepare_browser_editor(page, article, "오늘의 핫딜")
                out["editor_status"] = fields.get("status")
                out["editor_checks"] = fields
                if fields.get("status") != "EDITOR_INPUTS_FILLED_DRY_RUN":
                    actual = page.locator("div.ProseMirror[contenteditable=true]").inner_text()
                    expected = article["body"]
                    title_actual = page.locator('input[placeholder="제목을 입력해주세요."]').input_value()
                    out["editor_actual_length"] = len(normalized(actual))
                    out["editor_expected_length"] = len(normalized(expected))
                    out["title_matches"] = title_actual == article["title"]
                    out["actual_prefix"] = actual[:80]
                    out["actual_suffix"] = actual[-125:]
                    out["expected_suffix"] = expected[-125:]
                    page.screenshot(path=str(RESULT / "failed_editor.png"), timeout=9000)
                    raise RuntimeError("EDITOR_VALIDATION_FAILED")
                if urlsplit(page.url).path != "/i-gagyeog-mos-c/posts/new":
                    raise RuntimeError("WRONG_POST_EDITOR_ROUTE")
                submit = page.locator("button.w-full").filter(has_text=re.compile(r"^글쓰기$"))
                if submit.count() != 1 or not submit.is_enabled():
                    raise RuntimeError("UNIQUE_PUBLISH_BUTTON_MISSING")
                page.screenshot(path=str(RESULT / "before_click.png"), timeout=9000)
                out["publish_click_attempted"] = True
                submit.click(timeout=8000)
                page.wait_for_timeout(8000)
                final_url = urlsplit(page.url)
                final_text = page.locator("body").inner_text(timeout=5000)
                out["navigated_to_post"] = bool(
                    final_url.scheme == "https"
                    and final_url.hostname == "cafe.daangn.com"
                    and final_url.path.startswith("/i-gagyeog-mos-c/posts/")
                    and final_url.path != "/i-gagyeog-mos-c/posts/new"
                )
                out["title_visible"] = normalized(article["title"]) in normalized(final_text)
                out["sale_visible"] = "524,500원" in final_text
                out["owner_tag_visible"] = ("AF9668915" in final_text
                   or page.locator('a[href*="lptag=AF9668915"]').count() > 0)
                out["disclosure_visible"] = "쿠팡 파트너스 활동의 일환" in final_text
                out["stock_notice_visible"] = "실시간 핫딜 특성상" in final_text
                out["photo_present"] = page.locator("article img, [data-testid*=post] img").count() > 0 or page.locator("img").count() > 0
                if (out["navigated_to_post"] and out["title_visible"]
                    and out["sale_visible"] and out["owner_tag_visible"]
                    and out["disclosure_visible"] and out["stock_notice_visible"]):
                    out["post_url"] = page.url
                    out["post_published_verified"] = True
                    out["status"] = "PUBLISHED_VERIFIED"
                else:
                    out["status"] = "PUBLISH_UNCERTAIN_DO_NOT_RETRY"
                page.screenshot(path=str(RESULT / "after_click.png"), timeout=9000)
            finally:
                browser.close()
    except Exception as exc:
        out["status"] = ("PUBLISH_UNCERTAIN_DO_NOT_RETRY"
                         if out["publish_click_attempted"] else "ABORTED_BEFORE_PUBLISH")
        out["error_type"] = type(exc).__name__
        if isinstance(exc, (RuntimeError, ValueError)):
            out["error_reason"] = str(exc)[:120]
    return out

if __name__ == "__main__":
    RESULT.mkdir(exist_ok=True)
    outcome = publish_once()
    (RESULT / "result.json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print("DAANGN_ONE_NEW_FORMAT_RESULT " + json.dumps(outcome, ensure_ascii=False))
    if outcome["status"] != "PUBLISHED_VERIFIED":
        raise SystemExit(2)
