"""Tests for Jirum public HTML parsing, original purchase links, and Telegram send."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import jirum_forward as app

SAMPLE = """
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="jirum/94630">
  <div class="tgme_widget_message_bubble">
   <div class="tgme_widget_message_text js-message_text" dir="auto">
    ❤️초대박 14,100원 (25.8% 할인)<br/>
    카리어왕 치즈 앤 코코넛, 108g, 6개<br/>
    📌 가격 현황<br/>- 정상가: 19,010원<br/>- 할인: 4,910원<br/>
    📦 특가 바로가기<br/>
    <a href="https://link.coupang.com/a/iha660uGR2">https://link.coupang.com/a/iha660uGR2</a><br/>
    이 포스팅은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다.
   </div>
   <a class="tgme_widget_message_date" href="https://t.me/jirum/94630">date</a>
  </div>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="jirum/94631">
  <div class="tgme_widget_message_text js-message_text">
   👍대박 11,000원 (32.0% 할인)<br/>
   토스쇼핑 123 상품<br/>
   📦 특가 바로가기<br/>
   <a href="https://toss.im/_m/LLGfxTy3">특가 보러가기</a><br/>
   ✱ 이 포스팅은 토스쇼핑 쉐어링크 활동의 일환으로, 수수료를 제공받습니다.
  </div>
 </div>
</div>
<div class="tgme_widget_message" data-post="elsewhere/9999">
 <div class="tgme_widget_message_text">무관한 사이트</div>
</div>
"""


class RepostTests(unittest.TestCase):
    def test_public_html_parses_original_coupang_link_and_product(self):
        posts = app.parse_listing(SAMPLE)
        self.assertEqual([p["id"] for p in posts], [94630, 94631])
        self.assertEqual(posts[0]["title"], "카리어왕 치즈 앤 코코넛, 108g, 6개")
        self.assertEqual(posts[0]["product_url"], "https://link.coupang.com/a/iha660uGR2")
        self.assertIn("25.8% 할인", posts[0]["text"])
        self.assertIn("수수료", posts[0]["text"])
        self.assertNotIn("date", posts[0]["text"])

    def test_toss_anchor_label_still_returns_original_href(self):
        post = app.parse_listing(SAMPLE)[1]
        self.assertEqual(post["product_url"], "https://toss.im/_m/LLGfxTy3")
        self.assertEqual(post["title"], "토스쇼핑 123 상품")

    def test_no_bogus_telegram_channel_link_selected(self):
        self.assertIsNone(app.valid_product_url("https://t.me/jirum/123"))
        self.assertIsNone(app.valid_product_url("javascript:evil()"))
        self.assertIsNone(app.valid_product_url("http://localhost/test"))
        self.assertEqual(
            app.choose_product_link("📦 특가 바로가기", [("https://t.me/jirum/1", "")]),
            None,
        )

    def test_link_in_plain_text_without_anchor(self):
        p = app.parse_listing('''<div data-post="jirum/94632">
          <div class="tgme_widget_message_text">가격 안내<br>맥북<br>📦 특가 바로가기<br>
          https://link.coupang.com/a/hello</div></div>''')
        self.assertEqual(p[0]["product_url"], "https://link.coupang.com/a/hello")

    def test_missing_every_purchase_link_fails_closed(self):
        with self.assertRaises(RuntimeError):
            app.parse_listing('<div data-post="jirum/123"><div class="tgme_widget_message_text">아무 링크 없음</div></div>')

    def test_telegram_message_contains_clickable_original_url(self):
        post = app.parse_listing(SAMPLE)[0]
        message = app.make_message(post)
        self.assertTrue(message.startswith("🆕 [HOTDEAL]\n\n상품명: 카리어왕"))
        self.assertIn("상품링크: https://link.coupang.com/a/iha660uGR2", message)
        self.assertIn("25.8% 할인", message)
        self.assertLessEqual(len(message), 4096)

    def test_send_message_uses_original_link_and_existing_bot_secrets(self):
        post = app.parse_listing(SAMPLE)[1]
        with patch("telegram_delivery.target_chats", return_value=[("personal", "8888")]), patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "dummy-token",
            "TELEGRAM_CHAT_ID": "8888",
        }), patch.object(app.request, "urlopen", return_value=io.BytesIO(b'{"ok":true}')) as send:
            app.send_post(post)
        req = send.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/sendMessage"))
        self.assertNotIn("/forwardMessage", req.full_url)
        payload = parse_qs(req.data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], ["8888"])
        self.assertIn("상품링크: https://toss.im/_m/LLGfxTy3", payload["text"][0])
        self.assertEqual(payload["disable_web_page_preview"], ["true"])

    def test_first_live_run_records_baseline_without_reposting_latest(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "seen.json"):
                sent = []
                posts = lambda: app.parse_listing(SAMPLE)
                # Simulate a previous one-off verification message.
                app.run(mode="verify-repost", fetcher=posts,
                        sender=lambda p: sent.append(p["id"]))
                self.assertEqual(sent, [94631])
                # First real scheduled scan must not resend the same item.
                app.run(fetcher=posts, sender=lambda p: sent.append(p["id"]))
                app.run(fetcher=posts, sender=lambda p: sent.append(p["id"]))
                self.assertEqual(sent, [94631])
                self.assertEqual(app.load()["last_seen_id"], 94631)

    def test_preview_no_send_no_state(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "seen.json"):
                app.run(mode="preview", fetcher=lambda: app.parse_listing(SAMPLE),
                        sender=lambda _: self.fail("Should not send"))
                self.assertIsNone(app.load())

    def test_manual_verify_no_state_write(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "seen.json"):
                delivered = []
                app.run(mode="verify-repost", fetcher=lambda: app.parse_listing(SAMPLE),
                        sender=lambda p: delivered.append(p["id"]))
                self.assertEqual(delivered, [94631])
                self.assertIsNone(app.load())

    def test_new_posts_sent_once_and_ordered(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "seen.json"):
                app.save({"last_seen_id": 94629})
                sent = []
                for _ in range(2):
                    app.run(fetcher=lambda: app.parse_listing(SAMPLE),
                            sender=lambda p: sent.append(p["id"]))
                self.assertEqual(sent, [94630, 94631])
                self.assertEqual(app.load()["last_seen_id"], 94631)

    def test_error_does_not_mark_failed_message_delivered(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "seen.json"):
                app.save({"last_seen_id": 94629})
                def send(p):
                    if p["id"] == 94631:
                        raise RuntimeError("Telegram down")
                with self.assertRaises(RuntimeError):
                    app.run(fetcher=lambda: app.parse_listing(SAMPLE), sender=send)
                self.assertEqual(app.load()["last_seen_id"], 94630)

    def test_invalid_state_blocks_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "seen.json"):
                app.STATE.write_text(json.dumps({"last_seen_id": "invalid"}), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    app.run(fetcher=lambda: app.parse_listing(SAMPLE),
                            sender=lambda _: self.fail("Must not send"))


if __name__ == "__main__":
    unittest.main()
