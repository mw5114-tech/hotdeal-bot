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
                posts = {112029: "New", 112028: "Old"}
                for _ in range(2):
                    app.run(collector=lambda: posts, notifier=lambda i,t: sent.append((i,t)))
                self.assertEqual(sent, [(112029, "New")])
                self.assertEqual(set(app.load()["seen"]), {"112028", "112029"})

    def test_send_failure_retries_without_marking_seen(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                def boom(*args):
                    raise RuntimeError("Telegram failed")
                with self.assertRaises(RuntimeError):
                    app.run(collector=lambda: {112029: "New"}, notifier=boom)
                self.assertEqual(app.load()["seen"], {"112028": "Old"})

    def test_preview_new_posts_does_not_send_or_modify_history(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                app.run(preview=True, collector=lambda: {112029: "New"},
                        notifier=lambda *args: self.fail("Should not notify"))
                self.assertEqual(app.load()["seen"], {"112028": "Old"})

    def test_empty_or_invalid_response_never_changes_history(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "STATE", Path(d) / "history.json"):
                app.save({"seen": {"112028": "Old"}})
                with self.assertRaises(app.cafe.ProbeError):
                    app.run(collector=lambda: app.collect(fetcher=lambda: article_list()))
                self.assertEqual(app.load()["seen"], {"112028": "Old"})


if __name__ == "__main__":
    unittest.main()
