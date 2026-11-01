-- D1 idempotency log for Telegram group messages.
CREATE TABLE IF NOT EXISTS telegram_events (
  chat_id TEXT NOT NULL,
  message_id INTEGER NOT NULL,
  source_id INTEGER,
  status TEXT NOT NULL CHECK(status IN
    ('pending','dispatch_uncertain','dispatched','already_scanned')),
  created_at_ms INTEGER NOT NULL,
  PRIMARY KEY (chat_id,message_id)
);
CREATE INDEX IF NOT EXISTS telegram_events_pending
  ON telegram_events(status,created_at_ms,message_id);

-- Atomic lock prevents concurrent group messages and cron ticks from
-- sending repeated workflow_dispatch calls before GitHub shows a new run.
CREATE TABLE IF NOT EXISTS dispatch_lock (
  id INTEGER PRIMARY KEY CHECK(id=1),
  held_until_ms INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO dispatch_lock(id,held_until_ms) VALUES (1,0);
