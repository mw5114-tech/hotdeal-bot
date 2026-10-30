import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import naver_new_posts as app


def article_list(*ids):
    return json.dumps({"result": {"articleList": [
        {"type": "ARTICLE", "item": {"articleId": i, "subject": f"Deal {i}"}} for i in ids
    ], "noticeList": [{"articleId": 111, "subject": "Pinned"}]}}).encode()


class Tests(unittest.TestCase):
    def test_collect_public_article_metadata(self):
        with patch.object(app.cafe, "fetch", side_effect=AssertionError("No network request")):
            posts = app.collect(fetcher=lambda: article_list(112028, 112029))
        self.assertEqual(posts, {112028: "Deal 112028", 112029: "Deal 112029"})
        self.assertNotIn(111, posts)

    def test_baseline_sends_nothing_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                sent = []
                app.run(collector=lambda: {112028: "Deal"}, notifier=lambda *args: sent.append(args))
                self.assertEqual(sent, [])
                self.assertEqual(app.load()["seen"], {"112028": "Deal"})

    def test_preview_does_not_write_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.run(preview=True, collector=lambda: {112028: "Deal"},
                        notifier=lambda *args: self.fail("Should not notify"))
                self.assertFalse(app.STATE.exists())

    def test_only_new_posts_alert_once(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                sent = []
                posts = {112029: "[ㅋㅍ] New", 112028: "Old"}
                for _ in range(2):
                    app.run(collector=lambda: posts, notifier=lambda i,t: sent.append((i,t)))
                self.assertEqual(sent, [(112029, "[ㅋㅍ] New")])
                self.assertEqual(set(app.load()["seen"]), {"112028", "112029"})

    def test_send_failure_retries_without_marking_seen(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                def boom(*args):
                    raise RuntimeError("Telegram failed")
                with self.assertRaises(RuntimeError):
                    app.run(collector=lambda: {112029: "[ㅋㅍ] New"}, notifier=boom)
                self.assertEqual(app.load()["seen"], {"112028": "Old"})

    def test_preview_new_posts_does_not_send_or_modify_history(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                app.run(preview=True, collector=lambda: {112029: "[ㅋㅍ프레시] New"},
                        notifier=lambda *args: self.fail("Should not notify"))
                self.assertEqual(app.load()["seen"], {"112028": "Old"})

    def test_coupang_prefix_exact_only(self):
        accepted = ("[ㅋㅍ] 맥북 특가", "[ㅋㅍ프레시] 달걀", "  [ㅋㅍ] 세제")
        rejected = ("[네이버] 세일", "[11번가] 쿠팡 비교", "[ㅋㅍ일부회원] 특가",
                    "오늘 [ㅋㅍ] 특가", "[ㅋㅍ 프레시] 식품", "쿠팡 특가", "")
        for title in accepted:
            with self.subTest(title=title):
                self.assertTrue(app.is_coupang(title))
        for title in rejected:
            with self.subTest(title=title):
                self.assertFalse(app.is_coupang(title))

    def test_mixed_new_posts_only_coupang_alerts_and_ignored_are_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                sent = []
                posts = {
                    112032: "[네이버] 핫딜",
                    112031: "[ㅋㅍ] 가전",
                    112030: "[ㅋㅍ프레시] 과일",
                    112029: "[11번가] 할인",
                    112028: "Old",
                }
                for _ in range(2):
                    app.run(collector=lambda: posts,
                            notifier=lambda article_id, title: sent.append((article_id, title)))
                self.assertEqual(sent, [(112031, "[ㅋㅍ] 가전"), (112030, "[ㅋㅍ프레시] 과일")])
                self.assertEqual(set(app.load()["seen"]), {str(i) for i in range(112028, 112033)})

    def test_only_unrelated_posts_marks_seen_without_telegram(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                app.run(collector=lambda: {112029: "[알리] 기타 세일",
                                           112030: "[ㅋㅍ일부회원] 제외"},
                        notifier=lambda *args: self.fail("Non-Coupang post was sent"))
                self.assertEqual(set(app.load()["seen"]), {"112028", "112029", "112030"})

    def test_preview_mixed_posts_does_not_change_history(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                app.run(preview=True,
                        collector=lambda: {112029: "[ㅋㅍ프레시] 새 글",
                                           112030: "[네이버] 새 글"},
                        notifier=lambda *args: self.fail("Preview sent message"))
                self.assertEqual(app.load()["seen"], {"112028": "Old"})

    def test_send_failure_preserves_ignored_but_retries_matching(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                with self.assertRaises(RuntimeError):
                    app.run(collector=lambda: {112030: "[네이버] 세일",
                                               112029: "[ㅋㅍ] 신규"},
                            notifier=lambda *args: (_ for _ in ()).throw(RuntimeError("Telegram down")))
                self.assertEqual(app.load()["seen"], {"112028": "Old", "112030": "[네이버] 세일"})

    def test_empty_or_invalid_response_never_changes_history(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                with self.assertRaises(app.cafe.ProbeError):
                    app.run(collector=lambda: app.collect(fetcher=lambda: article_list()))
                self.assertEqual(app.load()["seen"], {"112028": "Old"})


if __name__ == "__main__":
    unittest.main()
