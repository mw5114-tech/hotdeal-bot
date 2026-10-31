"""No-network tests for exact source-item image lookup."""
import html
import json
import unittest
from gh_only.indexed_image_probe import (
    PRODUCT_ID, ITEM_ID, parse_bing_image_html,
    source_identity, safe_url
)

def make_card(murl, purl):
    meta = html.escape(json.dumps({"murl":murl,"purl":purl}),quote=True)
    return '<a class="iusc" m="' + meta + '"></a>'

IMAGE="https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/retail/test.jpg"

class IndexedImageTests(unittest.TestCase):
    def test_exact_source_url_is_only_candidate(self):
        exact=make_card(IMAGE,"https://www.coupang.com/vp/products/"+PRODUCT_ID+"?itemId="+ITEM_ID)
        result=parse_bing_image_html(exact)
        self.assertEqual(result["cards_seen"],1)
        self.assertEqual(len(result["matching_product_and_item"]),1)
        candidate=result["matching_product_and_item"][0]
        self.assertEqual(candidate["image_url_candidate"],IMAGE)
        self.assertFalse(result["exact_photo_content_verified"])

    def test_other_item_id_excluded(self):
        card=make_card(IMAGE,"https://www.coupang.com/vp/products/"+PRODUCT_ID+"?itemId=99999")
        result=parse_bing_image_html(card)
        self.assertEqual(len(result["matching_product_and_item"]),0)
        self.assertEqual(result["product_only_count"],1)

    def test_product_id_without_item_id_excluded(self):
        card=make_card(IMAGE,"https://www.coupang.com/vp/products/"+PRODUCT_ID)
        self.assertEqual(parse_bing_image_html(card)["product_only_count"],1)

    def test_unrelated_merchant_image_excluded(self):
        card=make_card(IMAGE,"https://www.megamart.com/product/01056471")
        result=parse_bing_image_html(card)
        self.assertEqual(len(result["matching_product_and_item"]),0)
        self.assertEqual(result["other_count"],1)

    def test_fake_bad_url_excluded(self):
        self.assertIsNone(safe_url("http://example.com/test.png"))
        self.assertIsNone(safe_url("https://evil@example.com/test.png"))
        self.assertEqual(source_identity("https://evil.example/vp/products/"+PRODUCT_ID+
                                        "?itemId="+ITEM_ID),"OTHER_SOURCE_URL")

    def test_html_escaping_still_parses(self):
        source="https://www.coupang.com/vp/products/"+PRODUCT_ID+"?itemId="+ITEM_ID+"&sourceType=search"
        result=parse_bing_image_html(make_card(IMAGE,source))
        self.assertEqual(len(result["matching_product_and_item"]),1)

if __name__=="__main__":
    unittest.main()
