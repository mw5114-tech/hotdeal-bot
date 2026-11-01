# Existing Oracle receiver → GitHub FIFO

`oracle_dispatch.py` uses Python's standard library. It does not call Telegram,
change webhooks, share a Telethon session, alter the receiver database, or restart
the existing `hotdeal-live` / `hotdeal-receiver` services. Cloudflare is unnecessary
when this service is deployed. Never arm both dispatchers simultaneously.

## Data and safety

- Read `/var/lib/hotdeal-receiver/inbox.sqlite3` with SQLite `mode=ro` every five
  seconds. Match the configured chat and relay bot; ignore test/non-Coupang rows.
- Preserve the exact body, group ID, message ID and purchase link. The optional
  existing relay outbox maps group delivery receipts to original `@jirum` IDs.
  Missing source IDs do not cause group-only products to disappear.
- Freeze each accepted message in a separate WAL/FULL SQLite journal. Unique
  event keys deduplicate retries. Existing receiver rows are never modified.
- Before every GitHub POST, commit a durable `uncertain` attempt. A unique index
  allows one outstanding request, including backlog-only requests. A lost response
  or crash requires manual review; elapsed time never makes it retryable.
- A 204 response becomes `accepted`. Match its UUID against the workflow run name.
  Missing/ambiguous run visibility blocks dispatch. Only confirmed success allows
  another request. Failure, cancellation or FIFO `in_flight` blocks automation.
- GitHub first stores the payload and consumed request UUID in its existing FIFO
  state with SHA compare-and-swap. Replayed UUIDs fail before publishing.
- Negative queue IDs identify group messages in a permanently bound single-group
  namespace. Public Jirum IDs/watermark retain their existing meaning. Appending
  new group rows preserves older FIFO items. Original source IDs and exact URLs
  deduplicate known public/group overlap. Different short URLs for the same SKU
  are not proven equivalent by this layer.
- Public manual scans remain available. Worker/Oracle-triggered runs consume the
  durable group payload and backlog without relying on a public Telegram page.
- Existing publisher, photo/product checks and claim/finish logic remain in use.
  Incomplete product evidence causes failure and review, never an invented price.

## Deployment sequence

1. Use the existing pinned SSH host key. Inspect active units, cron, source DB
   schemas, current receiver heartbeat, service users, and Always Free instance
   shape before installation. Do not stop or reconfigure either existing bot.
2. Record the reviewed group message baseline. Old inbox history must not be
   blindly published. Configure `DISPATCH_AFTER_MESSAGE_ID` explicitly and keep it
   immutable after first start. Inspect existing GitHub published/FIFO records.
3. Install `oracle_dispatch.py` into a new `/opt/hotdeal-github-dispatch` directory
   and the dedicated systemd unit in `deploy/hotdeal-github-dispatch.service`.
   The existing `hotdeal-live` user can read its own two source databases; systemd
   exposes their directories read-only to this new process.
4. Create root-owned mode 0600 `/etc/hotdeal-github-dispatch.env` with literal values:

   ```ini
   ENABLE_DISPATCH=false
   TELEGRAM_GROUP_CHAT_ID=<verified group>
   TRUSTED_RELAY_BOT_ID=<verified relay>
   DISPATCH_AFTER_MESSAGE_ID=<reviewed baseline>
   RECEIVER_DB_PATH=/var/lib/hotdeal-receiver/inbox.sqlite3
   RELAY_DB_PATH=/var/lib/hotdeal-live/jirum-live.sqlite3
   DISPATCH_DB_PATH=/var/lib/hotdeal-github-dispatch/dispatch.sqlite3
   GITHUB_TOKEN=<repository-limited Actions write and Contents read token>
   ```

   Do not put secrets in git, logs, shell history or command arguments. No Telegram
   token is needed by the dispatcher. Confirm the actual relay DB path on the VM.
5. Validate the unit, enable/start **only** `hotdeal-github-dispatch`, then verify
   capture-only operation and both old services' unchanged process IDs/start times.
6. Check real incoming relay messages, preserved source IDs/text, mock tests, FIFO
   state and current cafe posts. Verify the account's Actions included allowance
   and zero-paid-usage budget before setting `ENABLE_DISPATCH=true`.
7. Restart only the new service and inspect journal state, accepted request UUID,
   actual Actions run URL and final FIFO receipt. Test a restart of the new service
   without restarting the VM or disrupting existing bots.

## Budget and recovery

Default cap: 12 attempts in a rolling 24 hours, checked against both GitHub runs
and the local journal. Waiting events remain pending until a slot is available.
Idle checks happen at most once per minute; local inbox changes trigger an earlier
check. Idle operation launches zero Actions runs. No paid services are introduced.
The daily cap alone does not guarantee that the account's monthly included Actions
allowance is sufficient, particularly with other workflows. Verify billing limits.

For `uncertain`, retain all records and inspect GitHub runs, consumed UUIDs and the
cafe before any operator reconciliation. Never delete the journal or merely clear
the status to try again. Back up SQLite using its online backup API, not a copy of
the live database alone. A lost disk also loses local deduplication records.

## Offline validation

```sh
python -m unittest -v test_oracle_dispatch gh_only.test_jirum_manual_queue gh_only.test_telegram_group_queue
node --test cloudflare/jirum-telegram-webhook/src/index.test.mjs
```

These tests do not prove VM reachability, current bot-to-bot mode, real Telegram
receipt, a live GitHub dispatch, or actual cafe publication. Those checks must be
reported separately.
