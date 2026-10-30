"""Public Naver Cafe new-post watcher. No Naver login or cookies required."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
from urllib import request
from urllib.parse import urlencode
import naver_cafe_probe as cafe

STATE = Path(__file__).resolve().parent / "state" / "naver_seen_articles.json"
MAX_ALERTS = 10
KST = timezone(timedelta(hours=9))
ARTICLE_URL = "https://cafe.naver.com/f-e/cafes/24651941/articles/{}"


COUPANG_PREFIXES = ("[ㅋㅍ]", "[ㅋㅍ프레시]")


def is_coupang(title):
    """Only exact Coupang labels at the very beginning of a post title."""
    return isinstance(title, str) and title.lstrip().startswith(COUPANG_PREFIXES)


def collect(fetcher=None):
    """Read metadata from the first, newest-first, all-articles page."""
    body = cafe.fetch() if fetcher is None else fetcher()
    articles = cafe.parse_listing(body)
    if not articles:
        raise RuntimeError("Empty first page; refusing to reset or notify")
    print(f"NAVER_LIST: first_page_articles={len(articles)}")
    return articles


def load():
    if not STATE.exists():
        return None
    saved = json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or not isinstance(saved.get("seen"), dict):
        raise RuntimeError("Invalid Naver history; refusing to reset")
    return saved


def save(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE)


def notify(article_id, title):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("Telegram secrets are missing")
    url = ARTICLE_URL.format(article_id)
    msg = "🆕 [네이버 카페 새 핫딜 글]\n📝 " + title[:180] + "\n🔗 " + url
    data = urlencode({"chat_id": chat, "text": msg, "disable_web_page_preview": "true"}).encode()
    req = request.Request("https://api.telegram.org/bot" + token + "/sendMessage",
                          data=data, method="POST")
    try:
        with request.urlopen(req, timeout=20) as resp:
            result = json.load(resp)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Telegram rejected message")
    except Exception:
        raise RuntimeError("Telegram delivery failed (credentials hidden)") from None


def run(preview=False, collector=collect, notifier=notify):
    articles = collector()
    state = load()
    if state is None:
        print(f"NAVER_BASELINE: existing={len(articles)} zero messages")
        if preview:
            print("NAVER_PREVIEW: baseline not saved")
        else:
            save({"created_at": datetime.now(KST).isoformat(),
                  "seen": {str(article_id): title for article_id, title in articles.items()}})
        return 0
    seen = state["seen"]
    fresh = [(article_id, title) for article_id, title in articles.items()
             if str(article_id) not in seen]
    coupang = [(article_id, title) for article_id, title in fresh if is_coupang(title)]
    excluded = [(article_id, title) for article_id, title in fresh if not is_coupang(title)]
    print(f"NAVER_SCAN: seen={len(seen)} fresh={len(fresh)} "
          f"coupang_new={len(coupang)} excluded={len(excluded)} preview={preview}")
    # Record non-Coupang posts as observed, so edited titles won't cause old-post alerts.
    # Never change state in preview. Keep not-yet-sent Coupang posts unseen.
    if not preview and excluded:
        for article_id, title in excluded:
            seen[str(article_id)] = title
        save(state)
    # On an overloaded interval, unsent Coupang posts can be retried next scan.
    for article_id, title in coupang[:MAX_ALERTS]:
        if preview:
            print(f"NAVER_PREVIEW_NEW_COUPANG: id={article_id}")
        else:
            notifier(article_id, title)
            seen[str(article_id)] = title
            save(state)
            print(f"NAVER_SENT_COUPANG: id={article_id}")
    print(f"NAVER_ALERTS: processed={min(len(coupang), MAX_ALERTS)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    raise SystemExit(run(preview=args.preview))
