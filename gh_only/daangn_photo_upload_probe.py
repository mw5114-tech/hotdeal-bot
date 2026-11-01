"""Read-only browser probe: upload photo to an UNSUBMITTED Daangn editor.

No post submission, edit, deletion, or FIFO mutation. Browser is discarded.
Uses image artifact from the earlier #41271685261 run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from gh_only.daangn_editor_probe import parse_daangn_storage_state

TARGET = "https://cafe.daangn.com/i-gagyeog-mos-c/posts/new"
PHOTO = Path("photo-probe/jirum_94744_kakao_og.jpg")


def main() -> None:
    from playwright.sync_api import sync_playwright

    if not PHOTO.is_file():
        raise RuntimeError("PHOTO_ARTIFACT_NOT_FOUND")
    state = parse_daangn_storage_state(
        os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON", "")
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                storage_state=state, locale="ko-KR",
                viewport={"width": 1366, "height": 950},
            )
            page = context.new_page()
            responses = []

            def observe_response(resp):
                parsed = urlsplit(resp.url)
                path = parsed.path.lower()
                if (len(responses) < 70 and (
                    resp.status >= 400 or
                    any(word in path for word in
                        ("upload", "image", "photo", "media", "file"))
                )):
                    responses.append({
                        "status": resp.status,
                        "host": parsed.hostname,
                        "path_start": parsed.path[:100],
                    })

            page.on("response", observe_response)
            r = page.goto(
                TARGET, wait_until="domcontentloaded", timeout=30000
            )
            page.wait_for_timeout(1400)
            if (not r or r.status != 200
                or urlsplit(page.url).path != "/i-gagyeog-mos-c/posts/new"):
                raise RuntimeError("UNEXPECTED_EDITOR_REDIRECT")
            board = page.locator("select")
            if board.count() == 1:
                board.select_option(label="🔥 오늘의 핫딜")
            editor = page.locator("div.ProseMirror[contenteditable=true]")
            if editor.count() != 1:
                raise RuntimeError("EDITOR_NOT_FOUND")
            add_image = page.get_by_role("button", name="이미지 추가")
            if add_image.count() != 1:
                raise RuntimeError("IMAGE_BUTTON_AMBIGUOUS")
            with page.expect_file_chooser(timeout=11000) as chooser:
                add_image.click(timeout=7000)
            chooser.value.set_files(str(PHOTO.resolve()))
            measurements = []

            for pause in (0, 2, 5, 10, 18):
                if pause:
                    page.wait_for_timeout(pause * 1000)
                image_state = editor.locator("img").evaluate_all(
                    """(els)=>els.map(e=>({
                        complete:e.complete,
                        width:e.naturalWidth,height:e.naturalHeight,
                        alt:(e.alt||'').slice(0,40)
                    }))"""
                )
                submit = page.get_by_role(
                    "button", name="글쓰기", exact=True
                )
                buttons = []
                rect = editor.bounding_box()
                for i in range(submit.count()):
                    b = submit.nth(i)
                    box = b.bounding_box() if b.is_visible() else None
                    if box and rect and box["y"] > rect["y"]:
                        buttons.append({"enabled": b.is_enabled()})
                measurements.append({
                    "elapsed_since_probe_start_s": sum(
                        (0, 2, 5, 10, 18)[:len(measurements) + 1]
                    ),
                    "pending_label": "준비중" in editor.inner_text(),
                    "editor_image_count": len(image_state),
                    "editor_images": image_state,
                    "editor_submit_candidates": buttons,
                })
                print("DAANGN_PHOTO_PROBE_STEP " + json.dumps(
                    measurements[-1], ensure_ascii=False
                ))
            page.screenshot(
                path="daangn-photo-upload-probe.png",
                full_page=False, timeout=12000
            )
            print("DAANGN_PHOTO_PROBE_RESPONSES " + json.dumps(
                responses, ensure_ascii=False
            ))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
