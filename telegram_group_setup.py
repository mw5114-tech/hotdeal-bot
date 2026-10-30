"""Identify the user's already-added bot group using Telegram Bot API updates.

Manual fallback if the join event expired: send /chatid to the bot INSIDE
the "핫딜 MUSE용" Telegram group, then rerun the setup workflow.
"""
import json
import os
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

from telegram_delivery import GROUP_STATE, send_text

TARGET_GROUP = "핫딜 MUSE용"


def telegram_json(method, values):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN secret is missing")
    req = request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(values).encode(),
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=25) as response:
            payload = json.load(response)
    except error.HTTPError as e:
        raise RuntimeError(f"Telegram {method} failed (HTTP {e.code}); credentials hidden") from None
    except Exception:
        raise RuntimeError(f"Telegram {method} connection failed; credentials hidden") from None
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(f"Telegram {method} did not succeed")
    return payload["result"]


def find_group_id(updates):
    for update in reversed(updates):
        if not isinstance(update, dict):
            continue
        for name in ("my_chat_member", "message"):
            event = update.get(name, {})
            chat = event.get("chat", {}) if isinstance(event, dict) else {}
            if (chat.get("title") == TARGET_GROUP
                    and chat.get("type") in ("group", "supergroup")
                    and type(chat.get("id")) is int and chat["id"] < 0):
                if name == "my_chat_member":
                    member = event.get("new_chat_member", {})
                    if member.get("status") in ("left", "kicked"):
                        continue
                return chat["id"]
    return None


def run():
    updates = telegram_json("getUpdates", {
        "timeout": 0,
        "limit": 100,
        "allowed_updates": json.dumps(["message", "my_chat_member"]),
    })
    group_id = find_group_id(updates)
    if group_id is None:
        raise RuntimeError(
            "Could not identify '핫딜 MUSE용' from pending Telegram bot updates. "
            "Send /chatid (or /chatid@YourBotUsername) INSIDE that group, "
            "then rerun 'Configure Telegram group delivery'."
        )
    info = telegram_json("getChat", {"chat_id": group_id})
    if (info.get("title") != TARGET_GROUP or
            info.get("type") not in ("group", "supergroup") or
            info.get("id") != group_id):
        raise RuntimeError("Telegram group identity verification failed")
    # Do not write persistent config unless the group's test message succeeded.
    os.environ["TELEGRAM_GROUP_CHAT_ID"] = str(group_id)
    send_text("✅ 핫딜 봇 개인 + 핫딜 MUSE용 그룹 알림 연결 테스트")
    GROUP_STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = GROUP_STATE.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"chat_id": group_id, "title": TARGET_GROUP}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, GROUP_STATE)
    print("GROUP_SETUP_SUCCESS: verified matching group; personal and group test messages sent")


if __name__ == "__main__":
    run()
