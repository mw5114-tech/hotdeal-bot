"""Repost new public @jirum deals with the ORIGINAL purchase link (no Bot API forward).

Read message text/links from the channel's public t.me/s/jirum page.
Do not redirect or rewrite affiliate URLs, and never pretend this is a Telegram forward.
"""
import argparse
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from urllib import error, request
from urllib.parse import urlencode, urlsplit

PUBLIC_URL = "https://t.me/s/jirum"
STATE = Path(__file__).resolve().parent / "state" / "jirum_last_forwarded.json"
MAX_ALERTS = 30
MAX_MESSAGE_LENGTH = 4096
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
DATA_POST = re.compile(r"jirum/(\d+)")
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}


def valid_product_url(value):
    """Only expose external web links, never internal Telegram/navigation links."""
    if not isinstance(value, str):
        return None
    value = unescape(value).strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if (parsed.scheme not in ("https", "http") or not hostname
            or "." not in hostname or hostname in ("localhost",)
            or hostname == "t.me" or hostname.endswith(".t.me")
            or hostname == "telegram.org" or hostname.endswith(".telegram.org")
            or parsed.username or parsed.password or len(value) > 1500):
        return None
    return value


def choose_product_link(text, links):
    """Prefer the exact clickable href associated with the post's purchase link."""
    options = []
    for href, label in links:
        safe = valid_product_url(href)
        if safe and safe not in options:
            options.append(safe)
    visible_urls = [valid_product_url(m.group(0).rstrip(").,，。")) for m in URL_PATTERN.finditer(text)]
    visible_urls = [u for u in visible_urls if u]
    # Source posts put the purchase URL after the '특가 바로가기' marker.
    purchase_section = text.split("특가 바로가기", 1)
    if len(purchase_section) == 2:
        for u in visible_urls:
            if u in purchase_section[1]:
                # If displayed URL is a label for a hyperlink, prefer its actual href.
                if u in options:
                    return u
                # The web text is itself a direct purchase URL.
                return u
    if options:
        return options[0]
    return visible_urls[0] if visible_urls else None


def product_title(text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if (line.startswith(("📌", "📦", "- ", "🔥선구매", "이 포스팅은", "✱ 이 포스팅은"))
                or line.startswith(("http://", "https://"))
                or line.startswith(("상품링크:", "특가 바로가기"))):
            continue
        # '👍대박 14,100원 (25% 할인)' is a price label, not the product name.
        if "원" in line and ("할인" in line or "대박" in line or "무지성" in line):
            continue
        return line[:180]
    return ""


class PublicPosts(HTMLParser):
    """Capture only .tgme_widget_message_text inside each data-post='jirum/N'."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.post_depth = None
        self.text_depth = None
        self.active_anchor = None
        self.current = None
        self.posts = []

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if tag == "br":
            if self.text_depth is not None:
                self.current["parts"].append("\n")
            return
        if tag in VOID_TAGS:
            return
        if tag == "div":
            self.depth += 1
            if self.current is None:
                found = DATA_POST.fullmatch(props.get("data-post", ""))
                if found:
                    self.current = {"id": int(found.group(1)), "parts": [], "links": []}
                    self.post_depth = self.depth
            if (self.current is not None and self.text_depth is None
                    and "tgme_widget_message_text" in props.get("class", "").split()):
                self.text_depth = self.depth
        if self.text_depth is not None and tag == "a":
            self.active_anchor = {"href": props.get("href", ""), "label": []}

    def handle_data(self, data):
        if self.text_depth is not None and self.current is not None:
            self.current["parts"].append(data)
            if self.active_anchor is not None:
                self.active_anchor["label"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.active_anchor is not None and self.current is not None:
            label = "".join(self.active_anchor["label"])
            self.current["links"].append((self.active_anchor["href"], label))
            self.active_anchor = None
        if tag != "div":
            return
        if self.text_depth is not None and self.depth == self.text_depth:
            self.text_depth = None
        if self.current is not None and self.depth == self.post_depth:
            raw = "".join(self.current["parts"])
            # Keep Telegram's meaningful text line breaks, but not widget chrome.
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            text = "\n".join(lines)
            url = choose_product_link(text, self.current["links"])
            self.posts.append({
                "id": self.current["id"],
                "text": text,
                "title": product_title(text),
                "product_url": url,
            })
            self.current = None
            self.post_depth = None
        if self.depth:
            self.depth -= 1


def parse_listing(html):
    parser = PublicPosts()
    parser.feed(html)
    posts = {post["id"]: post for post in parser.posts if post["id"] > 0}
    if not posts:
        raise RuntimeError("No public @jirum posts found; refusing to update history")
    ordered = [posts[post_id] for post_id in sorted(posts)]
    if not any(post["product_url"] for post in ordered):
        raise RuntimeError("Jirum purchase links missing from ALL visible posts; refusing to update history")
    return ordered


def fetch_listing():
    req = request.Request(PUBLIC_URL, headers={
        "User-Agent": "Mozilla/5.0 (compatible; PublicDealReader/1.0)",
        "Accept": "text/html",
    })
    with request.urlopen(req, timeout=25) as response:
        if urlsplit(response.geturl()).hostname != "t.me":
            raise RuntimeError("Unexpected Telegram listing redirect")
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise RuntimeError("Telegram public listing response too large")
    return parse_listing(body.decode("utf-8-sig", "replace"))


def load():
    if not STATE.exists():
        return None
    saved = json.loads(STATE.read_text(encoding="utf-8"))
    if (not isinstance(saved, dict) or type(saved.get("last_seen_id")) is not int
            or saved["last_seen_id"] <= 0):
        raise RuntimeError("Invalid Jirum history; refusing to reset baseline")
    return saved


def save(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, STATE)


def make_message(post):
    """AI-friendly fields first; retain public source text for context and disclosures."""
    title = post["title"] or f"뽐질 핫딜 #{post['id']}"
    url = post["product_url"]
    if not url:
        raise ValueError("Missing purchase URL")
    head = f"🆕 [HOTDEAL]\n\n상품명: {title}\n\n상품링크: {url}"
    # Avoid pasting the product URL twice; original source copy still includes
    # discount details and affiliate disclosure when present.
    source = post["text"].replace(url, "")
    source = "\n".join(x.strip() for x in source.splitlines() if x.strip())
    if source:
        remaining = MAX_MESSAGE_LENGTH - len(head) - len("\n\n원문:\n")
        if remaining > 0:
            head += "\n\n원문:\n" + source[:remaining]
    return head[:MAX_MESSAGE_LENGTH]


def send_post(post):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("Telegram secrets are missing")
    payload = urlencode({
        "chat_id": chat,
        "text": make_message(post),
        "disable_web_page_preview": "true",
    }).encode()
    req = request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=25) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        # Never print a token-bearing endpoint or raw response.
        raise RuntimeError(f"Telegram sendMessage failed (HTTP {exc.code}; credentials hidden)") from None
    except Exception:
        raise RuntimeError("Telegram sendMessage request failed (credentials hidden)") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Telegram sendMessage rejected the post")


def run(mode="run", fetcher=fetch_listing, sender=send_post):
    posts = fetcher()
    if not posts:
        raise RuntimeError("Empty Jirum listing")
    posts = sorted(posts, key=lambda p: p["id"])
    latest = posts[-1]["id"]
    print(f"JIRUM_LIST: count={len(posts)} first={posts[0]['id']} last={latest} mode={mode}")
    if mode == "verify-repost":
        deal = next((p for p in reversed(posts) if p["product_url"]), None)
        if deal is None:
            raise RuntimeError("No deal with a purchase URL is visible")
        sender(deal)
        print(f"JIRUM_VERIFY_REPOSTED: post={deal['id']}; state_unchanged=true")
        return 0
    state = load()
    if state is None:
        if mode == "preview":
            print("JIRUM_PREVIEW: baseline missing; no send and no state change")
        else:
            # One recent item proves that a working PURCHASE link reaches the bot.
            deal = next((p for p in reversed(posts) if p["product_url"]), None)
            if deal is None:
                raise RuntimeError("No purchase link available for baseline verification")
            sender(deal)
            save({"last_seen_id": latest, "initialized_at": datetime.now(timezone.utc).isoformat()})
            print(f"JIRUM_BASELINE_REPOSTED: post={deal['id']} latest_seen={latest}; older skipped")
        return 0
    fresh = [p for p in posts if p["id"] > state["last_seen_id"]]
    print(f"JIRUM_SCAN: previous={state['last_seen_id']} fresh={len(fresh)} mode={mode}")
    for post in fresh[:MAX_ALERTS]:
        if mode == "preview":
            print(f"JIRUM_PREVIEW_POST: {post['id']} link_present={bool(post['product_url'])}")
            continue
        if post["product_url"]:
            sender(post)
            print(f"JIRUM_REPOSTED: post={post['id']}")
        else:
            print(f"JIRUM_SKIPPED_NO_LINK: post={post['id']}")
        state["last_seen_id"] = post["id"]
        save(state)
    if len(fresh) > MAX_ALERTS:
        print(f"JIRUM_PENDING: {len(fresh) - MAX_ALERTS} posts deferred")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("run", "preview", "verify-repost"), default="run")
    options = parser.parse_args()
    raise SystemExit(run(mode=options.mode))
