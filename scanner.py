"""Owner-permitted DealSpot watcher. Python 3.11 standard library only.
No Coupang API, no automatic Daangn posts or affiliate-link generation.
"""
import argparse
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from html import unescape
import json
import os
from pathlib import Path
import re
import time
from urllib import request
from urllib.parse import urljoin, urlsplit, urlencode

BASE = "https://deal.korea-inc.com"
LIST = BASE + "/deals/?view=drop"
STATE = Path(__file__).resolve().parent / "state" / "sent.json"
KST = timezone(timedelta(hours=9))
MAX_HTML = 2_000_000
INTEREST = (
    "라면", "과자", "생수", "커피", "음료", "즉석밥", "햇반", "간편식", "만두",
    "식품", "마스크팩", "클렌징", "선크림", "화장품", "샴푸", "바디",
    "충전기", "보조배터리", "멀티탭", "키보드", "물티슈", "휴지", "치약",
    "칫솔", "수납", "욕실", "세제", "주방", "도마", "냄비", "프라이팬",
    "육아", "유아", "기저귀", "에어프라이어", "청소기", "생활", "텀블러",
)
EXCLUDE = ("전자담배", "담배", "성인용", "영양제", "비타민", "다이어트약", "자동차", "타이어")

class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.anchors = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if self.skip:
            return
        if tag == "a":
            self.anchors.append([dict(attrs).get("href", ""), []])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "a" and self.anchors:
            h, parts = self.anchors.pop()
            self.links.append((h, " ".join(parts)))

    def handle_data(self, data):
        if self.skip or not data.strip():
            return
        self.parts.append(data)
        for anchor in self.anchors:
            anchor[1].append(data)

def clean(s):
    return re.sub(r"\s+", " ", unescape(s or "")).strip()

def allowed(url, detail=False):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != "deal.korea-inc.com" or p.username or p.password or p.port not in (None, 443):
        return False
    return p.path.startswith("/deal/") if detail else p.path == "/deals/" and p.query == "view=drop"

class SafeRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl, detail=("/deal/" in urlsplit(req.full_url).path)):
            raise RuntimeError("unexpected redirect outside permitted DealSpot paths")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(url, detail=False):
    if not allowed(url, detail):
        raise ValueError("unapproved URL")
    req = request.Request(url, headers={"User-Agent": "HotdealWatch/4.1 (site-owner permission)", "Accept": "text/html"})
    with request.build_opener(SafeRedirect()).open(req, timeout=20) as response:
        if not allowed(response.geturl(), detail):
            raise RuntimeError("unapproved redirected URL")
        data = response.read(MAX_HTML + 1)
    if len(data) > MAX_HTML:
        raise ValueError("oversized page")
    return data.decode("utf-8-sig", "replace")

def listing(html):
    p = Page()
    p.feed(html)
    urls = {}
    for href, title in p.links:
        url = urljoin(BASE, href)
        if allowed(url, detail=True):
            u = urlsplit(url)
            canonical = BASE + u.path
            urls.setdefault(canonical, clean(title))
    if not urls:
        raise ValueError("no product links: DealSpot HTML may have changed")
    return list(urls)

def money(content, label):
    found = re.search(label + r"\s*([\d,]+)\s*원", content)
    return int(found.group(1).replace(",", "")) if found else None

def detail(html, url):
    p = Page()
    p.feed(html)
    content = clean(" ".join(p.parts))
    header = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.S | re.I)
    if not header:
        return None
    h = Page()
    h.feed(header.group(1))
    name = clean(" ".join(h.parts))[:200]
    if not name or any(t in name for t in EXCLUDE) or not any(t in name for t in INTEREST):
        return None
    price = money(content, r"(?:현재\s*가격|현재\s*판매가)")
    average = money(content, r"최근\s*30일\s*평균\s*추적가")
    count = re.search(r"최근\s*30일\s*유효\s*추적\s*(\d+)\s*회", content)
    stamp = re.search(r"(20\d{2})\.(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s*수집", content)
    if not price or not average or not count or not stamp:
        return None
    try:
        measured = datetime(*map(int, stamp.groups()), tzinfo=KST)
    except ValueError:
        return None
    return dict(url=url, name=name, price=price, average=average,
                samples=int(count.group(1)), measured=measured)

def score(d, now, min_drop=20, max_price=100000, max_age=48):
    if d["price"] < 100 or d["price"] > max_price or d["samples"] < 3:
        return None
    age = (now - d["measured"]).total_seconds() / 3600
    if age < -1 or age > max_age or d["average"] <= d["price"]:
        return None
    drop = round((d["average"] - d["price"]) / d["average"] * 100, 1)
    if drop < min_drop:
        return None
    d["drop"] = drop
    d["score"] = min(100, round(45 + drop * 1.2 + min(d["samples"], 10) * 0.7))
    return d

def load_state(path=STATE):
    if not path.exists():
        return {}
    obj = json.loads(path.read_text("utf-8"))
    if not isinstance(obj, dict) or len(obj) > 20000:
        raise ValueError("invalid alert history")
    return obj

def save_state(state, path=STATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, path)

def telegram(token, chat_id, message):
    if not token or not chat_id:
        raise ValueError("missing GitHub Secrets")
    body = urlencode({"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}).encode()
    req = request.Request("https://api.telegram.org/bot" + token + "/sendMessage", data=body, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            result = json.load(response)
        if result.get("ok") is not True:
            raise RuntimeError("Telegram rejected the message")
    except Exception:
        # Never include token-bearing URL in logs or errors.
        raise RuntimeError("Telegram request failed; inspect token, chat ID or network") from None

def message(d):
    return ("\U0001f525 [이 가격 못 참지!] 딜스팟 핫딜 후보\n"
            "\U0001f6d2 " + d["name"] + "\n"
            "\U0001f4b0 마지막 추적가: " + format(d["price"], ",") + "원\n"
            "\U0001f4ca 30일 평균 추적가: " + format(d["average"], ",") + "원\n"
            "\U0001f4c9 평균가 대비: " + str(d["drop"]) + "% 하락\n"
            "\u2b50 추천점수: " + str(d["score"]) + "/100\n"
            "\U0001f552 수집시각(KST): " + d["measured"].strftime("%Y-%m-%d %H:%M") + "\n"
            "\U0001f517 " + d["url"] + "\n"
            "\u26a0\ufe0f 쿠팡 실제 결제 가격·배송비·재고는 직접 확인하세요. 제휴 링크가 아닙니다.")

def run(preview=False):
    now = datetime.now(KST)
    print("DealSpot scan:", now.isoformat(), "preview:", preview)
    try:
        urls = listing(fetch(LIST))
    except Exception as exc:
        print("Failed to retrieve deal listing:", type(exc).__name__, str(exc)[:160])
        return 2
    candidates = []
    failures = 0
    for url in urls[:30]:
        if len(candidates) >= 10:
            break
        try:
            raw = detail(fetch(url, detail=True), url)
            if preview:
                if raw is None:
                    print("SKIP_PARSE_OR_CATEGORY:", url)
                else:
                    age_hours = (now - raw["measured"]).total_seconds() / 3600
                    fall = round((raw["average"] - raw["price"]) / raw["average"] * 100, 1) if raw["average"] > 0 else 0
                    reasons = []
                    if raw["price"] < 100 or raw["price"] > 100000:
                        reasons.append("price_out_of_range")
                    if raw["samples"] < 3:
                        reasons.append("insufficient_samples")
                    if age_hours < -1 or age_hours > 48:
                        reasons.append("outdated_price")
                    if raw["average"] <= raw["price"] or fall < 20:
                        reasons.append("insufficient_price_drop")
                    print("CHECK:", raw["name"][:45], "price=", raw["price"],
                          "samples=", raw["samples"], "age_hours=", round(age_hours, 1),
                          "drop_pct=", fall, "result=", ",".join(reasons) or "PASS")
            d = score(raw, now) if raw else None
            if d:
                candidates.append(d)
        except Exception as exc:
            failures += 1
            print("Detail parse/network problem:", type(exc).__name__)
        time.sleep(0.7)
    candidates.sort(key=lambda d: (-d["score"], -d["drop"]))
    history = load_state()
    alerts = 0
    for d in candidates:
        key = urlsplit(d["url"]).path.rstrip("/")
        previous = history.get(key)
        if isinstance(previous, int) and d["price"] >= previous:
            continue
        if alerts >= 5:
            break
        if preview:
            print("\nPREVIEW:\n" + message(d))
        else:
            telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                     os.environ.get("TELEGRAM_CHAT_ID", ""), message(d))
            history[key] = d["price"]
            save_state(history)
            print("sent:", d["name"][:70])
        alerts += 1
    print("listing:", len(urls), "qualified:", len(candidates), "alert candidates:", alerts,
          "detail failures:", failures)
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true", help="No Telegram alerts, no state changes")
    args = parser.parse_args()
    raise SystemExit(run(args.preview))
