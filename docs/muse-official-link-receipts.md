# Muse → GitHub official Coupang Partners link receipt

GitHub keeps scanning Telegram Jirum every 5 minutes. It publishes only after Muse writes a real, officially generated /a/ link receipt. Never post synthetic /re/AFFSDP candidates.

## Muse workflow
1. Read state/daangn_jirum_manual_fifo.json on main.
2. For each pending row use its exact id (Telegram @jirum message ID), not Muse internal queue numbering.
3. If now minus posted_at_utc >= 60 minutes, skip; GitHub will archive it automatically.
4. In the existing logged-in Coupang Partners tab, use the official 간편 링크 만들기 form with the clean product+item URL.
5. Write one UTF-8 JSON file named links/<telegram_message_id>.json to main with this shape:

```json
{
  "telegram_message_id": 94890,
  "telegram_posted_at": "2026-11-03T10:10:00+09:00",
  "product_id": "9440621860",
  "item_id": "28512619980",
  "partners_link": "https://link.coupang.com/a/il321hccMe",
  "issued_at": "2026-11-03T10:15:00+09:00",
  "status": "LINK_READY"
}
```

IMPORTANT: Example values are illustrative. Never use this sample file for a real dispatch. Copy the real pending row timestamp and source ID and officially issued shortlink. Add optional vendor_item_id when known.

6. GitHub push to links/*.json triggers the existing Daangn queue workflow. No receipt means no posting; invalid receipt means reject. GitHub independently resolves the source product+item and compares receipt identity before posting.
7. A deal aged >=60 minutes at publish time is archived in skipped_expired and never submitted, even if a receipt was already issued.
8. If login/browser expires, stop generating links and notify the operator. GitHub continues collection and expiry.

## First handshake and limits
Muse's connector may require human approval for each GitHub write. This is not fully unattended yet. Start with one harmless file write outside links/, e.g. manual/muse-write-check.txt; do not edit live queues for a test.
Only after that has been verified should Muse write real official receipts. Do not include credentials, browser session data, or cookies.
The receipt verifies consistency of metadata, not actual commission attribution.
