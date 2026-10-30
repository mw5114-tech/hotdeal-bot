"""Durable per-destination outbox. No Telegram credentials are stored here."""
import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS posts (
            scope TEXT, id INTEGER, body TEXT NOT NULL, revisions INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(scope,id));
          CREATE TABLE IF NOT EXISTS delivery (
            scope TEXT, post_id INTEGER, destination TEXT, part INTEGER, chat TEXT NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
            remote_id INTEGER, reason TEXT,
            PRIMARY KEY(scope,post_id,destination,part));
        ''')

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def initialize(self, latest, targets):
        if type(latest) is not int or latest < 0:
            raise ValueError("INVALID_BASELINE")
        targets = [list(item) for item in targets]
        saved = self.get("targets")
        if saved is not None and saved != targets:
            raise RuntimeError("DESTINATIONS_CHANGED_REVIEW_REQUIRED")
        with self.db:
            self.set("targets", targets)
            if self.get("baseline") is None:
                self.set("baseline", latest)
                self.set("cursor", latest)

    def recover(self):
        # A crash after Bot API accepted a request but before receipt persistence
        # cannot be distinguished from a lost send. Never silently repeat it.
        with self.db:
            self.db.execute("UPDATE delivery SET status='uncertain',reason='PROCESS_INTERRUPTED' WHERE status='sending'")

    def put(self, post_id, body, parts, targets, *, scope="live", scanned=False):
        if scope == "live" and (self.get("baseline") is None or post_id <= self.get("baseline")):
            return
        with self.db:
            previous = self.db.execute("SELECT body FROM posts WHERE scope=? AND id=?", (scope, post_id)).fetchone()
            if previous is None:
                self.db.execute("INSERT INTO posts(scope,id,body) VALUES (?,?,?)", (scope, post_id, body))
            elif previous[0] != body:
                self.db.execute("UPDATE posts SET body=?,revisions=revisions+1 WHERE scope=? AND id=?", (body, scope, post_id))
            attempted = self.db.execute("SELECT 1 FROM delivery WHERE scope=? AND post_id=? AND attempts>0 LIMIT 1", (scope, post_id)).fetchone()
            if attempted is None:
                self.db.execute("DELETE FROM delivery WHERE scope=? AND post_id=?", (scope, post_id))
                for label, chat in targets:
                    for part, text in enumerate(parts):
                        self.db.execute("INSERT INTO delivery(scope,post_id,destination,part,chat,text) VALUES (?,?,?,?,?,?)",
                                        (scope, post_id, label, part, chat, text))
            if scanned and scope == "live":
                self.set("cursor", max(post_id, self.get("cursor")))

    def claim(self, scope="live", now=None):
        now = time.time() if now is None else now
        with self.db:
            row = self.db.execute('''SELECT d.* FROM delivery d
                WHERE d.scope=? AND d.status IN ('pending','retry') AND d.next_at<=?
                  AND NOT EXISTS (SELECT 1 FROM delivery p WHERE p.scope=d.scope
                    AND p.post_id=d.post_id AND p.destination=d.destination
                    AND p.part<d.part AND p.status!='sent')
                ORDER BY d.post_id,d.part,d.destination LIMIT 1''', (scope, now)).fetchone()
            if row:
                self.db.execute("UPDATE delivery SET status='sending',attempts=attempts+1 WHERE scope=? AND post_id=? AND destination=? AND part=?", self.key(row))
        return dict(row) if row else None

    @staticmethod
    def key(row):
        return tuple(row[k] for k in ("scope", "post_id", "destination", "part"))

    def finish(self, row, status, *, remote_id=None, reason=None, delay=0):
        if status not in {"sent", "retry", "uncertain", "blocked"}:
            raise ValueError("INVALID_STATUS")
        with self.db:
            self.db.execute("UPDATE delivery SET status=?,remote_id=?,reason=?,next_at=? WHERE scope=? AND post_id=? AND destination=? AND part=?",
                            (status, remote_id, reason, time.time() + delay, *self.key(row)))

    def resolve(self, scope, post_id, destination, part, outcome):
        if outcome not in {"sent", "retry"}:
            raise ValueError("INVALID_RESOLUTION")
        with self.db:
            changed = self.db.execute("UPDATE delivery SET status=?,reason='OPERATOR_RESOLVED',next_at=0 WHERE scope=? AND post_id=? AND destination=? AND part=? AND status IN ('uncertain','blocked')",
                                      (outcome, scope, post_id, destination, part)).rowcount
        if changed != 1:
            raise ValueError("NO_REVIEWABLE_DELIVERY")

    def summary(self):
        counts = {f"{r[0]}:{r[1]}": r[2] for r in self.db.execute("SELECT scope,status,count(*) FROM delivery GROUP BY scope,status")}
        review = [dict(r) for r in self.db.execute("SELECT scope,post_id,destination,part,status,reason FROM delivery WHERE status IN ('uncertain','blocked') LIMIT 30")]
        return {"baseline": self.get("baseline"), "cursor": self.get("cursor"), "counts": counts, "review": review}
