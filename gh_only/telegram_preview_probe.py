"""GitHub-only official Telegram web preview diagnostic.

Telegram's *user* MTProto messages.getWebPagePreview can return a cached web
page preview and photo without publishing the URL into any chat. This tool
does not impersonate any crawler and never accesses Coupang product HTML.
The link's preview may be absent or stale, and its photo is not proof that
the exact selected item/vendor option matches. No Daangn posting.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

KNOWN_LINK = "https://link.coupang.com/a/iiCmQxWdUG"
SHORT_PATH = re.compile(r"/a/[A-Za-z0-9]+/?")


def validate_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("INVALID_LINK")
    u = urlsplit(value)
    if (u.scheme != "https" or u.hostname != "link.coupang.com"
            or u.username or u.password or u.port not in (None, 443)
            or u.query or u.fragment or not SHORT_PATH.fullmatch(u.path)):
        raise ValueError("COUPANG_SHORTLINK_REQUIRED")
    return value


def summarize_preview(preview: object) -> dict:
    media = getattr(preview, "media", None)
    web = getattr(media, "webpage", None)
    if web is None:
        return {"state": "NO_WEBPAGE_PREVIEW", "has_photo": False,
                "title": None, "description": None, "resolved_host": None}
    link = getattr(web, "url", None)
    host = None
    if isinstance(link, str) and link.startswith("https://"):
        host = urlsplit(link).hostname
    photo = getattr(web, "photo", None)
    name = getattr(web, "title", None)
    description = getattr(web, "description", None)
    return {"state": "PREVIEW_PHOTO_PRESENT" if photo else "PREVIEW_WITHOUT_PHOTO",
            "has_photo": bool(photo),
            "title": str(name)[:180] if name else None,
            "description": str(description)[:300] if description else None,
            "resolved_host": host,
            "exact_item_and_vendor_verified": False,
            "image_republication_rights_verified": False}


async def run(link: str, image_output: Path, report_output: Path):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.messages import GetWebPagePreviewRequest

    validate_url(link)
    app_id = os.environ.get("TELEGRAM_API_ID", "")
    app_hash = os.environ.get("TELEGRAM_API_HASH", "")
    session = os.environ.get("TELEGRAM_STRING_SESSION", "")
    if not app_id.isdigit() or len(app_hash) != 32 or not session:
        raise RuntimeError("USER_TELEGRAM_SESSION_NOT_CONFIGURED")
    client = TelegramClient(StringSession(session), int(app_id), app_hash,
                            connection_retries=1, auto_reconnect=False)
    try:
        await asyncio.wait_for(client.connect(), timeout=25)
        if not await client.is_user_authorized():
            raise RuntimeError("TELEGRAM_USER_SESSION_EXPIRED")
        preview = await asyncio.wait_for(
            client(GetWebPagePreviewRequest(message=link)), timeout=30)
        summary = summarize_preview(preview)
        photo = getattr(getattr(getattr(preview, "media", None), "webpage", None),
                        "photo", None)
        summary["downloaded"] = False
        if photo:
            try:
                image_output.parent.mkdir(parents=True, exist_ok=True)
                path = await asyncio.wait_for(
                    client.download_media(photo, file=str(image_output)), timeout=30)
                if path:
                    dest = Path(path).resolve()
                    if (dest.is_file() and dest.parent == image_output.parent.resolve()
                            and 0 < dest.stat().st_size < 5_000_000):
                        summary["downloaded"] = True
                        summary["filename"] = dest.name
                        summary["bytes"] = dest.stat().st_size
            except Exception:
                summary["download_error"] = "DOWNLOAD_FAILED"
        report = {
            "schema": "github-telegram-link-preview.v1",
            "tested_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "official_telegram_mtproto_preview",
            "source_link": link,
            "target_product_id": "6221369038",
            "target_item_id": "12453073752",
            **summary,
            "publish_ready": False,
            "messages_sent": 0,
        }
        report_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print("TELEGRAM_PREVIEW " + json.dumps({
            "state": summary["state"],
            "has_photo": summary["has_photo"],
            "downloaded": summary["downloaded"],
            "exact_option_verified": False,
            "messages_sent": 0
        }))
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=KNOWN_LINK)
    parser.add_argument("--image", default="telegram_preview_candidate.jpg")
    parser.add_argument("--report", default="telegram_preview_report.json")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.url, Path(args.image), Path(args.report)))
    except Exception as exc:
        # Keep secrets and Telegram internals out of logs.
        print("TELEGRAM_PREVIEW_NOT_AVAILABLE " + type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
