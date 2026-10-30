import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import new_items as app

def html(*paths):
    return "<html><body>" + "".join('<a href="/deal/'+p+'/">'+p+'</a>' for p in paths) + "</body></html>"

class Tests(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(app.canonical("/deal/abc/"),"https://deal.korea-inc.com/deal/abc/")
        self.assertIsNone(app.canonical("https://other.example/deal/abc/"))
    def test_collect_dedupe_and_page(self):
        pages=[html("a","b"), html("b","c"),html("b","c")]
        def fake(url):
            page=int(app.parse_qs(app.urlsplit(url).query).get("page",["1"])[0])
            return pages[page-1]
        result=app.collect(fetch=fake, max_pages=10, pause=0)
        self.assertEqual(len(result),3)
    def test_first_run_no_alert(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app,"STATE",Path(d)/"seen.json"),patch.object(app,"collect",return_value={"https://deal.korea-inc.com/deal/a/":"Item A"}),patch.object(app,"notify",side_effect=AssertionError("should not send")):
                self.assertEqual(app.run(),0)
                self.assertEqual(len(app.load()["seen"]),1)
    def test_only_new_sent_once(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/"seen.json"
            with patch.object(app,"STATE",state):
                app.save({"created_at":"test","seen":{"https://deal.korea-inc.com/deal/a/":"Item A"}})
                calls=[]
                catalog={"https://deal.korea-inc.com/deal/a/":"Item A","https://deal.korea-inc.com/deal/b/":"Item B"}
                with patch.object(app,"collect",return_value=catalog),patch.object(app,"notify",side_effect=lambda n,u: calls.append(u)):
                    app.run()
                    app.run()
                self.assertEqual(calls,["https://deal.korea-inc.com/deal/b/"])
    def test_send_failure_not_marked_seen(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app,"STATE",Path(d)/"seen.json"):
                app.save({"created_at":"test","seen":{}})
                with patch.object(app,"collect",return_value={"https://deal.korea-inc.com/deal/a/":"Item A"}),patch.object(app,"notify",side_effect=RuntimeError("send error")):
                    with self.assertRaises(RuntimeError):
                        app.run()
                self.assertEqual(app.load()["seen"],{})

if __name__ == "__main__":
    unittest.main()
