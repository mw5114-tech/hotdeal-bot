"""No-network Telegram MTProto link-preview contracts."""
from types import SimpleNamespace
import unittest
from gh_only.telegram_preview_probe import validate_url, summarize_preview, KNOWN_LINK


class PreviewTests(unittest.TestCase):
    def test_known_shortlink(self):
        self.assertEqual(validate_url(KNOWN_LINK), KNOWN_LINK)

    def test_invalid_link_not_accepted(self):
        urls = ("http://link.coupang.com/a/test",
                "https://evil.example/a/test",
                "https://link.coupang.com.evil.example/a/test",
                "https://link.coupang.com@evil.example/a/test",
                "https://link.coupang.com/a/test?x=1",
                "https://link.coupang.com/a/../test")
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_url(url)

    def test_empty_media(self):
        self.assertEqual(summarize_preview(SimpleNamespace(media=None))["state"],
                         "NO_WEBPAGE_PREVIEW")

    def test_webpage_with_photo_is_only_candidate(self):
        page = SimpleNamespace(title="오뚜기 핫케이크 믹스 1kg 1개",
                               description="쇼핑 상품", photo=object(),
                               url="https://www.coupang.com/vp/products/6221369038?itemId=12453073752")
        media = SimpleNamespace(webpage=page)
        result = summarize_preview(SimpleNamespace(media=media))
        self.assertTrue(result["has_photo"])
        self.assertEqual(result["state"], "PREVIEW_PHOTO_PRESENT")
        self.assertFalse(result["exact_item_and_vendor_verified"])
        self.assertFalse(result["image_republication_rights_verified"])

    def test_page_without_photo(self):
        page = SimpleNamespace(title="상품", description="설명", photo=None, url="https://www.coupang.com/")
        result = summarize_preview(SimpleNamespace(media=SimpleNamespace(webpage=page)))
        self.assertFalse(result["has_photo"])

    def test_title_length_bounded(self):
        page = SimpleNamespace(title="a" * 300, description="b" * 400,
                               photo=object(), url="http://example.com")
        result = summarize_preview(SimpleNamespace(media=SimpleNamespace(webpage=page)))
        self.assertEqual(len(result["title"]), 180)
        self.assertEqual(len(result["description"]), 300)
        self.assertIsNone(result["resolved_host"])


if __name__ == "__main__":
    unittest.main()
