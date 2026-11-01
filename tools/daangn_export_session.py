"""One-time LOCAL, visible owner login to export Daangn storage state for Actions.

Never logs passwords, QR codes, cookies or session data. Does not post.
Run on user's PC (headful Chromium), inspect it, then paste saved JSON into
GitHub Actions repo secret DAANGN_CAFE_STORAGE_STATE_JSON. Do not commit it.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cafe-url", default="https://cafe.daangn.com/")
    ap.add_argument("--output", default=str(Path.home() / "daangn_cafe_storage_state.json"))
    args = ap.parse_args()
    uri = urlsplit(args.cafe_url)
    if uri.scheme != "https" or uri.hostname != "cafe.daangn.com" or uri.username or uri.password:
        raise ValueError("Only https://cafe.daangn.com/... is supported")
    target = Path(args.output).expanduser().resolve()
    if target.exists():
        raise RuntimeError("Storage export already exists; refuse overwrite without manual removal")
    target.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "umask"):
        os.umask(0o077)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            ctx = browser.new_context(locale="ko-KR")
            page = ctx.new_page()
            page.goto(args.cafe_url, wait_until="domcontentloaded", timeout=30_000)
            print("열린 브라우저에서 본인 당근 계정으로 직접 로그인하고,")
            print("본인 카페에서 글쓰기 버튼이 보이는 상태까지 이동한 다음 Enter를 누르셈.")
            input("직접 로그인 확인 후 Enter: ")
            current = urlsplit(page.url)
            if current.hostname != "cafe.daangn.com":
                raise RuntimeError("Current page is not Daangn Cafe; no state exported")
            state = ctx.storage_state(indexed_db=True)
            if not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
                raise RuntimeError("Storage state is incomplete")
            provider = [c for c in state["cookies"] if c.get("domain", "").lstrip(".").endswith("daangn.com")]
            if not provider and not state["origins"]:
                raise RuntimeError("No provider session data found; nothing exported")
            target.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            if os.name != "nt":
                target.chmod(0o600)
            print("저장 경로:", target)
            print("JSON 내용은 채팅/커밋에 보내지 말고 GitHub Actions Secret에만 등록하셈.")
            print("로그인 유지 기간/기기 확인은 당근 쪽 정책에 따라 달라질 수 있음.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
