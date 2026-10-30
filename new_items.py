"""DealSpot new-item watcher, no price or category filters. Python 3.11 stdlib."""
import argparse
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from html import unescape
import json
import os
from pathlib import Path
import re
import time
from urllib import request, error
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode, unquote

BASE = "https://deal.korea-inc.com"
LIST_PATH = "/deals/"
STATE = Path(__file__).resolve().parent / "state" / "seen_products.json"
MAX_PAGES = 80
MAX_ALERTS = 10
KST = timezone(timedelta(hours=9))

class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.a = []
        self.stack = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag == "a" and not self.skip:
            self.stack.append([dict(attrs).get("href", ""), []])
    def handle_data(self, data):
        if not self.skip:
            for a in self.stack:
                a[1].append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self.stack:
            self.a.append(self.stack.pop())
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

def canonical(href):
    url = urljoin(BASE, href)
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != "deal.korea-inc.com":
        return None
    if not p.path.startswith("/deal/") or p.path.rstrip("/") == "/deal":
        return None
    return BASE + p.path.rstrip("/") + "/"

def get(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != "deal.korea-inc.com" or p.path != LIST_PATH:
        raise ValueError("Unexpected URL")
    q = parse_qs(p.query)
    if any(k != "page" for k in q):
        raise ValueError("Unexpected query")
    req = request.Request(url, headers={"User-Agent":"DealSpot-NewItem-Watcher/1.0 (owner permitted)","Accept":"text/html"})
    with request.urlopen(req, timeout=25) as r:
        # Do not automatically consume content from a different host.
        if urlsplit(r.geturl()).hostname != "deal.korea-inc.com":
            raise ValueError("Unexpected redirect")
        data = r.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Oversized response")
    return data.decode("utf-8-sig", "replace")

def extract(html):
    p = Links()
    p.feed(html)
    items = {}
    for href, parts in p.a:
        url = canonical(href)
        if not url:
            continue
        title = re.sub(r"\s+", " ", unescape(" ".join(parts))).strip()
        if len(title) < 3:
            title = unquote(urlsplit(url).path.split("/")[-2]).replace("-", " ")
        items[url] = title[:180]
    return items

def collect(fetch=get, max_pages=1, pause=0):
    """Only watch the first /deals/ page (assumed newest-first)."""
    items = extract(fetch(BASE + LIST_PATH))
    if not items:
        raise RuntimeError("First listing page contained no product links")
    print(f"page=1 items={len(items)} unique_total={len(items)}")
    return items

def load():
    if not STATE.exists():
        return None
    value = json.loads(STATE.read_text("utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("seen"), dict):
        raise RuntimeError("Invalid state file; refusing to reset baseline")
    return value

def save(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE)

def notify(name, url):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("Telegram secrets are missing")
    msg = "🆕 [딜스팟 새 상품 등록]\n🛒 " + name + "\n🔗 " + url + "\n⚠️ 가격·할인율은 확인되지 않은 신규 등록 알림입니다."
    data = urlencode({"chat_id":chat,"text":msg,"disable_web_page_preview":"true"}).encode()
    req = request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data=data,method="POST")
    try:
        with request.urlopen(req, timeout=20) as resp:
            result = json.load(resp)
            if not result.get("ok"):
                raise RuntimeError("Telegram rejected message")
    except Exception:
        raise RuntimeError("Telegram delivery error; credentials hidden") from None

def run(preview=False):
    catalog = collect()
    state = load()
    now = datetime.now(KST).isoformat()
    if state is None:
        print(f"BASELINE: {len(catalog)} existing items recorded; zero messages sent")
        if not preview:
            save({"created_at":now, "seen":catalog})
        else:
            print("PREVIEW ONLY: baseline not saved")
        return 0
    seen = state["seen"]
    fresh = [(u, title) for u, title in catalog.items() if u not in seen]
    print(f"previously_seen={len(seen)} new_items={len(fresh)} preview={preview}")
    for url, name in fresh[:MAX_ALERTS]:
        if preview:
            print("PREVIEW NEW:", name, url)
        else:
            notify(name, url)
            seen[url] = name
            save(state)  # Record each successfully delivered item.
            print("SENT:", name[:75])
    if not preview:
        # Existing items are already in baseline. Don't mark unsent new items
        # as seen: they will be delivered on later runs.
        save(state)
    print("sent_or_previewed=", min(len(fresh), MAX_ALERTS))
    return 0

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args=ap.parse_args()
    raise SystemExit(run(args.preview))
