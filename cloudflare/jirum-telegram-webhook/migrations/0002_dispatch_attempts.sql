ALTER TABLE telegram_events ADD COLUMN payload_json TEXT;
CREATE TABLE dispatch_attempts (
  request_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK(status IN ('uncertain','accepted','completed')),
  created_at_ms INTEGER NOT NULL
);
-- Even an expired lease cannot create a second outstanding request.
CREATE UNIQUE INDEX one_active_dispatch ON dispatch_attempts((1))
  WHERE status != 'completed';
