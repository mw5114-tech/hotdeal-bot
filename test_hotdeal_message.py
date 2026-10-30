"""Exact Telegram payload contract consumed by downstream cafe-posting AI."""
import io
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import new_items
import naver_new_posts
from hotdeal_message import format_hotdeal_message


class HotdealMessageTests(unittest.TestCase):
    def test_one_canonical_message_for_ai(self):
        self.assertEqual(
            format_hotdeal_message("삼성 갤럭시 버즈3 프로", "https://example.com/item/123"),
            "🆕 [HOTDEAL]\n\n상품명: 삼성 갤럭시 버즈3 프로\n\n상품링크: https://example.com/item/123",
        )

    def test_title_newlines_cannot_create_fake_fields(self):
        msg = format_hotdeal_message(
            "  갤럭시\n  버즈3    프로  ", "https://example.com/item/123"
        )
        self.assertEqual(
            msg, "🆕 [HOTDEAL]\n\n상품명: 갤럭시 버즈3 프로\n\n상품링크: https://example.com/item/123"
        )

    def assert_sent_message(self, app, args, expected_text):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "1234",
        }):
            with patch.object(app.request, "urlopen", return_value=io.BytesIO(b'{"ok":true}')) as send:
                app.notify(*args)
        self.assertEqual(send.call_count, 1)
        request = send.call_args.args[0]
        payload = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(payload["text"], [expected_text])
        self.assertEqual(payload["chat_id"], ["1234"])
        self.assertEqual(payload["disable_web_page_preview"], ["true"])

    def test_dealspot_telegram_payload(self):
        url = "https://deal.korea-inc.com/deal/sample-item/"
        self.assert_sent_message(
            new_items,
            ("무선 이어폰", url),
            f"🆕 [HOTDEAL]\n\n상품명: 무선 이어폰\n\n상품링크: {url}",
        )

    def test_naver_telegram_payload(self):
        self.assert_sent_message(
            naver_new_posts,
            (112029, "[ㅋㅍ프레시] 우유 특가"),
            "🆕 [HOTDEAL]\n\n상품명: [ㅋㅍ프레시] 우유 특가\n\n"
            "상품링크: https://cafe.naver.com/f-e/cafes/24651941/articles/112029",
        )


if __name__ == "__main__":
    unittest.main()
