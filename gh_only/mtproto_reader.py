"""Optional GitHub-only Telegram MTProto source reader.

Reads media attached to the authenticated user's Telegram messages or channels.
Requires a previously authorized Telegram StringSession saved as a GitHub Secret.
NEVER requests a login code, sends any message, or publishes posts.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from gh_only.pipeline import identify_url, price_info
from jirum_live import extract

ALIAS_RE = re.compile(r"^@?[A-Za-z0-9_]{4,32}$")


def evidence_from_message(message) -> dict:
    post = extract(message)
    prices = price_info(post["text"])
    link = identify_url(post["product_url"])
    document = getattr(message, "document", None)
    mime = getattr(document, "mime_type", "") if document else ""
    photo_attached = bool(getattr(message, "photo", None))
    image_doc = bool(isinstance(mime, str) and mime.startswith("image/"))
    return {
        "source_message_id": post["id"],
        "title": post["title"],
        "original_purchase_url": post["product_url"],
        "source_image_attached": bool(photo_attached or image_doc),
        "source_image_provenance": ("telegram_mtproto_attachment" if photo_attached or image_doc
                                    else "no_attached_image"),
        "image_file": None,
        "image_verified_for_exact_option": False,
        "own_affiliate_url": None,
        "auto_publish_ready": False,
        "blockers": ["EXACT_OPTION_NOT_VERIFIED", "OWN_AFFILIATE_LINK_NOT_CREATED",
                     "PUBLISHER_NOT_INTEGRATED"],
        **prices, **link
    }


async def read_live(source: str, image_dir: Path, limit: int = 8) -> list[dict]:
    # Lazy dependency: pure/offline tests never require Telethon or credentials.
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = os.environ.get("TELEGRAM_API_ID", "")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    session = os.environ.get("TELEGRAM_STRING_SESSION", "")
    if not api_id.isdigit() or len(api_hash) != 32 or not session:
        raise RuntimeError("GITHUB_TELEGRAM_SECRETS_NOT_CONFIGURED")
    if not ALIAS_RE.fullmatch(source):
        raise RuntimeError("INVALID_TELEGRAM_SOURCE_USERNAME")
    client = TelegramClient(StringSession(session), int(api_id), api_hash,
                            connection_retries=1, auto_reconnect=False)
    records = []
    image_dir.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION_EXPIRED")
        target = await client.get_entity(source)
        messages = await client.get_messages(target, limit=limit)
        for message in sorted(messages, key=lambda m: m.id):
            record = evidence_from_message(message)
            if record["source_image_attached"]:
                name = "telegram_" + str(record["source_message_id"])
                # Media is only written into the ephemeral Action workspace.
                # This is not guaranteed to be the product's exact option image.
                try:
                    path = await asyncio.wait_for(
                        client.download_media(message, file=str(image_dir / name)), timeout=25)
                    if path:
                        real = Path(path).resolve()
                        if (real.parent == image_dir.resolve() and real.is_file()
                                and real.stat().st_size <= 10_000_000):
                            record["image_file"] = real.name
                        elif real.is_file() and real.parent == image_dir.resolve():
                            real.unlink()
                except Exception:
                    record["image_download_failed"] = True
            records.append(record)
        return records
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.environ.get("GH_ONLY_SOURCE_USERNAME") or "jirum")
    parser.add_argument("--output", default="gh_only_mtproto_report.json")
    parser.add_argument("--images", default="gh_only_mtproto_images")
    args = parser.parse_args()
    try:
        records = asyncio.run(read_live(args.source, Path(args.images)))
        result = {"schema": "github-only-telegram-mtproto.v1",
                  "created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "message_count": len(records),
                  "attached_image_count": sum(x["source_image_attached"] for x in records),
                  "downloaded_image_count": sum(bool(x["image_file"]) for x in records),
                  "publish_count": 0, "records": records}
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("MTPROTO_READ_OK " + json.dumps(
            {k: v for k, v in result.items() if k != "records"}, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Avoid printing Telethon exception bodies (could contain private metadata).
        print("MTPROTO_READ_FAILED " +
              ("EXPIRED_SESSION" if str(exc) == "TELEGRAM_SESSION_EXPIRED"
               else "MISSING_AUTH" if str(exc) == "GITHUB_TELEGRAM_SECRETS_NOT_CONFIGURED"
               else "READ_ERROR"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
