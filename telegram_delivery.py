"""Send one Telegram alert to the owner AND the configured private group.

The bot token and owner's chat ID stay in GitHub Actions Secrets. The private
group ID is saved in this private repository after a verified membership check.
"""
import json
import os
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

GROUP_STATE = Path(__file__).resolve().parent / "state" / "telegram_group_chat.json"


def target_chats():
    owner = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not owner:
        raise RuntimeError("Telegram owner's chat ID is not configured")
    group = os.environ.get("TELEGRAM_GROUP_CHAT_ID", "").strip()
    if not group and GROUP_STATE.exists():
        data = json.loads(GROUP_STATE.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or
                not isinstance(data.get("chat_id"), int) or
                data["chat_id"] >= 0 or
                data.get("title") != "핫딜 MUSE용"):
            raise RuntimeError("Invalid Telegram group configuration; refusing unknown destination")
        group = str(data["chat_id"])
    if group and (not group.lstrip("-").isdigit() or not group.startswith("-")):
        raise RuntimeError("Telegram group chat ID must be a negative integer")
    return [("personal", owner)] + ([("group", group)] if group and group != owner else [])


def send_text(text, *, urlopen=None):
    """Send unchanged text to all configured recipients, in order.

    If a later recipient rejects a message, the caller does not mark the item
    as delivered, so it can retry. A partial success may be repeated on retry.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Telegram bot token is missing")
    opener = urlopen or request.urlopen
    for label, chat_id in target_chats():
        payload = urlencode({
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            method="POST",
        )
        try:
            with opener(req, timeout=25) as resp:
                result = json.load(resp)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("Telegram sendMessage rejected the message")
        except error.HTTPError as ex:
            raise RuntimeError(
                f"Telegram {label} delivery rejected (HTTP {ex.code}; credentials hidden)"
            ) from None
        except Exception:
            raise RuntimeError(
                f"Telegram {label} delivery failed (credentials hidden)"
            ) from None
        print(f"TELEGRAM_DELIVERED: destination={label}")
