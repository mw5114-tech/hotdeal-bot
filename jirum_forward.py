"""Forward new public @jirum posts unchanged via Telegram Bot API.

Public web pages are used only to discover message IDs. Contents, formatting,
links, and media are passed through by Telegram's forwardMessage API itself.
"""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from urllib import error, request
from urllib.parse import urlencode, urlsplit

PUBLIC_URL = "https://t.me/s/jirum"
SOURCE_CHAT = "@jirum"
STATE = Path(__file__).resolve().parent / "state" / "jirum_last_forwarded.json"
MAX_FORWARD_PER_RUN = 30


class MessageIDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        # data-post is Telegram's own link between a rendered post and its ID.
        match = re.fullmatch(r"jirum/(\d+)", props.get("data-post", ""))
        if match:
            self.ids.add(int(match.group(1)))


def parse_listing(html):
    p = MessageIDs()
    p.feed(html)
    ids = sorted(i for i in p.ids if i > 0)
    if not ids:
        raise RuntimeError("No public @jirum message IDs found; refusing to reset history")
    return ids


def fetch_listing():
    req = request.Request(PUBLIC_URL, headers={
        "User-Agent": "Mozilla/5.0 (compatible; JirumPublicPostWatcher/1.0)",
        "Accept": "text/html",
    })
    with request.urlopen(req, timeout=25) as response:
        if urlsplit(response.geturl()).hostname != "t.me":
            raise RuntimeError("Unexpected Telegram listing redirect")
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise RuntimeError("Telegram public listing response is too large")
    return parse_listing(body.decode("utf-8-sig", "replace"))


def load():
    if not STATE.exists():
        return None
    data = json.loads(STATE.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("last_seen_id")) is not int
            or data["last_seen_id"] <= 0):
        raise RuntimeError("Invalid @jirum history; refusing to reset")
    return data


def save(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STATE)


def forward_message(message_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("Telegram bot token/chat ID missing")
    data = urlencode({
        "chat_id": chat,
        "from_chat_id": SOURCE_CHAT,
        "message_id": message_id,
    }).encode("utf-8")
    endpoint = f"https://api.telegram.org/bot{token}/forwardMessage"
    req = request.Request(endpoint, data=data, method="POST")
    try:
        with request.urlopen(req, timeout=25) as response:
            result = json.load(response)
    except error.HTTPError as ex:
        # Telegram supplies an actionable 'description' in failed Bot API responses.
        # Show only that description, NEVER the endpoint or its embedded bot token.
        try:
            payload = json.loads(ex.read(4096))
            description = payload.get("description", "") if isinstance(payload, dict) else ""
        except (ValueError, UnicodeDecodeError, OSError):
            description = ""
        if not isinstance(description, str):
            description = ""
        description = " ".join(description.split())[:240].replace(token, "[REDACTED]")
        # Avoid writing unexpected server-origin text or credentials to Actions logs.
        if not description.startswith(("Bad Request:", "Forbidden:", "Too Many Requests:")):
            description = "No safe Telegram error description available"
        raise RuntimeError(
            f"Telegram forwardMessage failed for @{SOURCE_CHAT[1:]} "
            f"post {message_id} (HTTP {ex.code}): {description}"
        ) from None
    except Exception:
        raise RuntimeError("Telegram forwardMessage request failed (credentials hidden)") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Telegram forwardMessage returned an unsuccessful response")


def run(mode="run", fetcher=fetch_listing, forwarder=forward_message):
    ids = fetcher()
    if not ids:
        raise RuntimeError("Empty @jirum listing; refusing to reset")
    ids = sorted(set(ids))
    state = load()
    print(f"JIRUM_LIST: count={len(ids)} first={ids[0]} last={ids[-1]} mode={mode}")
    if mode == "verify-forward":
        # Explicit manual verification; does not modify state.
        forwarder(ids[-1])
        print(f"JIRUM_VERIFY_SUCCESS: forwarded_post={ids[-1]} state_unchanged=true")
        return 0
    if state is None:
        if mode == "preview":
            print("JIRUM_PREVIEW: baseline not present; no messages and no state changes")
        else:
            # Forward only the latest visible post as a one-time real delivery check.
            # If it fails, preserve the uninitialized state and report the error.
            forwarder(ids[-1])
            save({"last_seen_id": ids[-1], "initialized_at": datetime.now(timezone.utc).isoformat()})
            print(f"JIRUM_BASELINE_VERIFIED: forwarded_latest={ids[-1]}; older posts skipped")
        return 0
    fresh = [post_id for post_id in ids if post_id > state["last_seen_id"]]
    print(f"JIRUM_SCAN: previous={state['last_seen_id']} new={len(fresh)} mode={mode}")
    # If more than a single batch arrives, stop rather than mark any unseen post as delivered.
    for message_id in fresh[:MAX_FORWARD_PER_RUN]:
        if mode == "preview":
            print(f"JIRUM_PREVIEW_POST: {message_id}")
        else:
            forwarder(message_id)
            state["last_seen_id"] = message_id
            save(state)
            print(f"JIRUM_FORWARDED: post_id={message_id}")
    if len(fresh) > MAX_FORWARD_PER_RUN:
        print(f"JIRUM_PENDING: {len(fresh) - MAX_FORWARD_PER_RUN} posts left for next scan")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("run", "preview", "verify-forward"), default="run")
    opts = parser.parse_args()
    raise SystemExit(run(mode=opts.mode))
