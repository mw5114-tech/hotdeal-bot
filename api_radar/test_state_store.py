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
