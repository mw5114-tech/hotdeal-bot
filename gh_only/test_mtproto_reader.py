"""MTProto adapter unit tests without logging in or hitting Telegram."""
from types import SimpleNamespace
import unittest

from gh_only.mtproto_reader import evidence_from_message, ALIAS_RE


class FakeMessage:
    def __init__(self, text, photo=None, document=None):
        self.id = 94711
        self.raw_text = text
        self.photo = photo
        self.document = document
        self.edit_date = None

    def get_entities_text(self):
        return []


class MtprotoTests(unittest.TestCase):
    def test_message_with_photo_returns_attachment_metadata(self):
        msg = FakeMessage(
            "쁘띠첼 포도\n판매가 14,100원\n"
            "https://www.coupang.com/vp/products/9412434217?itemId=25858815414",
            photo=object())
        record = evidence_from_message(msg)
        self.assertTrue(record["source_image_attached"])
        self.assertIsNone(record["image_file"])
        self.assertFalse(record["image_verified_for_exact_option"])
        self.assertEqual(record["sale_price_krw"], 14100)
        self.assertEqual(record["product_id"], "9412434217")
        self.assertEqual(record["item_id"], "25858815414")
        self.assertFalse(record["auto_publish_ready"])

    def test_image_document_detected(self):
        msg = FakeMessage("상품\n최종가 9,900원", document=SimpleNamespace(mime_type="image/jpeg"))
        self.assertTrue(evidence_from_message(msg)["source_image_attached"])

    def test_nonimage_document_rejected(self):
        msg = FakeMessage("상품\n판매가 9,900원", document=SimpleNamespace(mime_type="application/pdf"))
        self.assertFalse(evidence_from_message(msg)["source_image_attached"])

    def test_source_alias_limit(self):
        self.assertTrue(ALIAS_RE.fullmatch("@jirum"))
        self.assertFalse(ALIAS_RE.fullmatch("https://evil.example"))
        self.assertFalse(ALIAS_RE.fullmatch("@source/name"))


if __name__ == "__main__":
    unittest.main()
