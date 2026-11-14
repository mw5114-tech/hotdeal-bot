"""No-network tests for dedicated GitHub branch state persistence."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

from api_radar import collector as c
from api_radar import state_store as s


def sample():
    return {"id": "12345678", "name": "브랜드 무선키보드 저소음 제품",
            "price": 32900, "source": "goldbox",
            "is_rocket": True, "free_shipping": True}


class StateTests(TestCase):
    def test_independent_state_location(self):
        self.assertEqual(s.STATE_BRANCH, "api-radar-data")
        self.assertEqual(s.STATE_PATH, "state/api_radar_snapshot.json")
        self.assertNotIn("naver_blog_jirum", s.STATE_PATH)

    def test_load_404_creates_empty_in_memory(self):
        with patch.object(s, "api", return_value=None):
            state, sha = s.load_state()
        self.assertEqual(state["products"], {})
        self.assertIsNone(sha)

    def test_save_state_encodes_only_product_history(self):
        calls = []
        def fake_api(method, data):
            calls.append((method, data))
            return {"commit": {"sha": "fake"}}
        with patch.object(s, "api", side_effect=fake_api):
            s.save_state(c.empty_state(), None)
        method, body = calls[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(body["branch"], "api-radar-data")
        decoded = json.loads(base64.b64decode(body["content"]))
        self.assertNotIn("NAVER_BLOG_STORAGE_STATE_JSON", decoded)
        self.assertEqual(decoded["schema"], "api-radar-snapshot-v1")

    def test_populates_rank_report_without_post(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            record = {"schema": "api-radar-readonly-v1", "mode": "scan",
                      "all_observations": [sample()]}
            (folder / "summary.json").write_text(json.dumps(record))
            with patch.object(s, "load_state", return_value=(c.empty_state(), None)), \
                 patch.object(s, "save_state") as save:
                s.persist_report(folder)
                save.assert_called_once()
            ranked = json.loads((folder / "ranked.json").read_text())
            self.assertFalse(ranked["ranked_candidates"][0]["posting_eligible"])
            self.assertEqual(ranked["posts_published"], 0)

    def test_persists_official_goldbox_inventory_not_keyword_search(self):
        now = datetime(2026, 11, 14, 1, 15, tzinfo=timezone.utc)
        def observation(pid, source, name):
            return {"id": pid, "name": name, "price": 32500,
                    "source": source, "is_rocket": True, "free_shipping": True}
        obs = [observation("12345678", "goldbox", "골드박스 키보드 정품"),
               observation("22345678", "search", "검색 전용 키보드 정품")]
        metas = [{"id": p["id"], "url":
                  "https://link.coupang.com/re/AFFSDP?pageKey=" + p["id"]
                  + "&itemId=98765432", "image":
                  "https://image15.coupangcdn.com/x.jpg",
                  "category": "가전디지털", "price": 32500} for p in obs]
        record = {"schema": "api-radar-readonly-v1", "mode": "scan",
                  "checked_at_utc": now.isoformat(),
                  "all_observations": obs, "product_metadata": metas}
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "summary.json").write_text(json.dumps(record))
            saved = []
            with patch.object(s, "load_state", return_value=(c.empty_state(), None)), \
                 patch.object(s, "save_state", side_effect=lambda state,sha: saved.append(state)), \
                 patch.object(s, "datetime") as clock:
                clock.now.return_value = now
                s.persist_report(folder)
            queue = saved[0]["goldbox_today"]
            self.assertEqual(queue["kst_date"], "2026-11-14")
            self.assertEqual(set(queue["items"]), {"12345678"})
            self.assertEqual(queue["items"]["12345678"]["source"], "goldbox")
            self.assertEqual(queue["items"]["12345678"]["checked_at"], now.isoformat())
            self.assertNotIn("22345678", queue["items"])

    def test_new_day_replaces_goldbox_inventory_and_preserves_publication_history(self):
        t1 = datetime(2026, 11, 14, 1, 15, tzinfo=timezone.utc)
        t2 = datetime(2026, 11, 15, 1, 15, tzinfo=timezone.utc)
        state = c.empty_state()
        state["published"]["12345678"] = {
            "url":"https://blog.naver.com/mw5114/100001",
            "kst_date":"2026-11-14", "grade":"B"}
        states = []
        for t, pid in ((t1, "12345678"), (t2, "22345678")):
            ob = {"id":pid, "name":"로켓배송 생필품 테스트",
                  "price":15000, "source":"goldbox",
                  "is_rocket":True, "free_shipping":True}
            meta = {"id":pid, "url":
                    "https://link.coupang.com/re/AFFSDP?pageKey="
                    + pid + "&itemId=98765432",
                    "image":"https://image15.coupangcdn.com/x.jpg",
                    "category":"생활용품","price":15000}
            record={"schema":"api-radar-readonly-v1", "mode":"scan",
                    "checked_at_utc":t.isoformat(),
                    "all_observations":[ob], "product_metadata":[meta]}
            with tempfile.TemporaryDirectory() as td:
                folder=Path(td)
                (folder/"summary.json").write_text(json.dumps(record))
                with patch.object(s,"load_state",return_value=(state,"sha")), \
                     patch.object(s,"save_state",side_effect=lambda new,sha:states.append(new)), \
                     patch.object(s,"datetime") as clock:
                    clock.now.return_value=t
                    s.persist_report(folder)
            state=states[-1]
        self.assertEqual(state["goldbox_today"]["kst_date"],"2026-11-15")
        self.assertEqual(set(state["goldbox_today"]["items"]),{"22345678"})
        self.assertIn("12345678",state["published"])

    def test_invalid_input_fails_before_state_write(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            wrong = sample()
            wrong["price"] = -10
            (folder / "summary.json").write_text(json.dumps({
                "schema": "api-radar-readonly-v1", "mode": "scan",
                "all_observations": [wrong]}))
            with patch.object(s, "save_state") as save:
                with self.assertRaisesRegex(s.RadarStateError, "RADAR_UNSAFE_OBSERVATION"):
                    s.persist_report(folder)
                save.assert_not_called()

    def test_never_rewrites_telegram_and_naver_queues(self):
        self.assertTrue(s.STATE_PATH.startswith("state/api_radar_"))
        self.assertNotEqual(s.STATE_PATH, "state/naver_blog_jirum_fifo.json")
        self.assertNotEqual(s.STATE_PATH, "state/daangn_jirum_manual_fifo.json")


if __name__ == "__main__":
    main()
