"""Download only a photo attached to the exact public Jirum Telegram message.

A source-attached photo is not an independently verified Coupang option image.
Untrusted third-party link-preview images are excluded.
"""
from __future__ import annotations

from pathlib import Path
from urllib import request
from urllib.parse import urlsplit

MAX_PHOTO_BYTES = 2_500_000


def allowed_source_photo(url: str | None) -> bool:
    if not isinstance(url, str) or not 20 < len(url) <= 2500:
        return False
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        return (p.scheme == "https"
                and (host == "telesco.pe" or host.endswith(".telesco.pe")
                     or host == "telegram-cdn.org" or host.endswith(".telegram-cdn.org"))
                and p.port in (None, 443) and p.username is None and p.password is None
                and not p.fragment and p.path.startswith("/file/"))
    except ValueError:
        return False


def download_source_photo(url: str | None, output: Path, source_message_id: int,
                          opener=None) -> dict:
    if not allowed_source_photo(url):
        return {"status": "TELEGRAM_PHOTO_URL_UNTRUSTED", "saved_name": None}
    req = request.Request(url, headers={
        "Accept": "image/*", "User-Agent": "Mozilla/5.0",
    })
    try:
        op = opener or request.urlopen
        with op(req, timeout=20) as resp:
            if resp.status != 200 or resp.geturl() != url:
                return {"status": "TELEGRAM_PHOTO_REDIRECT_OR_STATUS",
                        "saved_name": None}
            mime = resp.headers.get_content_type()
            data = resp.read(MAX_PHOTO_BYTES + 1)
    except Exception as exc:
        return {"status": "TELEGRAM_PHOTO_DOWNLOAD_FAILED",
                "error_type": type(exc).__name__, "saved_name": None}
    ext = {"image/jpeg": ".jpg", "image/png": ".png",
           "image/webp": ".webp"}.get(mime)
    valid = ((ext == ".jpg" and data.startswith(bytes((255, 216, 255))))
             or (ext == ".png" and data.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))))
             or (ext == ".webp" and data.startswith(b"RIFF")
                 and data[8:12] == b"WEBP"))
    if not valid or not 1000 <= len(data) <= MAX_PHOTO_BYTES:
        return {"status": "TELEGRAM_PHOTO_CONTENT_INVALID", "saved_name": None}
    filename = f"jirum_{source_message_id}_telegram_source_photo{ext}"
    (output / filename).write_bytes(data)
    return {"status": "IMAGE_SAVED", "saved_name": filename,
            "image_source": "telegram_attached_photo", "bytes": len(data)}
