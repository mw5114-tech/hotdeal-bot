import json
from pathlib import Path
import tempfile
import unittest
from gh_only.daangn_drafts import build_drafts, clean_title, run, DISCLOSURE


def page(text, href="https://link.coupang.com/a/sourceCode", number=1):
    return (f'<div class="tgme_widget_message" data-post="jirum/{number}">'
            f'<div class="tgme_widget_message_text">{text}<br>📦 특가 바로가기<br>'
            f'<a href="{href}">{href}</a></div></div>')


class DraftTests(unittest.TestCase):
    def test_price_only_heading_not_product_name(self):
        for heading in ("☆특가 3,850원", "🔥🔥🔥무지성 3,180원 (49% 할인)", "❤️초대박 4,460원"):
            with self.subTest(heading=heading):
                self.assertEqual(clean_title(heading + "\n오뚜기 핫케이크 믹스"), "오뚜기 핫케이크 믹스")

    def test_original_affiliate_never_in_body(self):
        d = build_drafts(page("☆특가 3,850원<br>과자 96g 2개"))[0]
        self.assertNotIn("link.coupang.com", d["body"])
        self.assertNotIn("sourceCode", json.dumps(d))
        self.assertIn(DISCLOSURE, d["body"])
        self.assertFalse(d["publish_ready"])
        self.assertIn("원문에 표시된 가격: 3,850원", d["body"])

    def test_non_coupang_excluded(self):
        self.assertEqual(build_drafts(page("특가 1,000원<br>상품", "https://naver.me/example")), [])

    def test_lookalike_host_excluded(self):
        self.assertEqual(build_drafts(page("특가 1,000원<br>상품", "https://link.coupang.com.evil.test/a/code")), [])

    def test_conflicting_price_blocked(self):
        d = build_drafts(page("특가 1,000원<br>상품<br>판매가 2,000원"))[0]
        self.assertIn("SOURCE_PRICE_UNAVAILABLE", d["blockers"])
        self.assertIn("가격 확인 필요", d["body"])

    def test_missing_title_blocked(self):
        d = build_drafts(page("☆특가 1,000원"))[0]
        self.assertIn("PRODUCT_TITLE_UNAVAILABLE", d["blockers"])

    def test_source_links_in_title_removed(self):
        self.assertIsNone(clean_title("상품 https://example.org/a"))

    def test_text_artifact_no_active_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.html"
            source.write_text(page("특가 1,000원<br>테스트 상품"), encoding="utf-8")
            out = Path(tmp) / "drafts"
            self.assertEqual(run(out, source), 1)
            self.assertNotIn("https://", (out / "drafts.txt").read_text(encoding="utf-8"))
            report = json.loads((out / "drafts.json").read_text(encoding="utf-8"))
            self.assertEqual(report["posts_published"], 0)


if __name__ == "__main__":
    unittest.main()
