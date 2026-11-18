"""Cross-run exact-item Lowchart snapshot: title, actual photo and native chart.

Only a successful, public exact productId/itemId Lowchart page may publish a
snapshot. Photo and chart bytes are GitHub *blob objects* (not third-party
links): the same original bytes can be consumed by the sibling runner even
when Lowchart itself returns HTTP 403. The immutable manifest lives on the
existing isolated cache branch. No credentials, posting content, or arbitrary
HTML/images are ever stored. Everything is bound to one Telegram message,
verified source URL, product ID, item ID, short TTL and SHA256 digests.

Do not mistake a successful GitHub upload for a successful product lookup.
Failures are best effort and must not relax the publisher's photo gate.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
from urllib import error, request

from gh_only.lowchart_price_reference import build_url
from gh_only.lowchart_product_photo import is_coupang_cdn_url
from gh_only.lowchart_shared_reference import (
    CACHE_BRANCH, REPOSITORY, create_record, verified_history,
    MAX_AGE_SECONDS,
)
from gh_only.lowchart_chart_capture import _inspect_native_png

SCHEMA = "verified-lowchart-bundle-v1"
PREFIX = "state/verified_lowchart_bundle"
MAX_PHOTO_BYTES = 2_500_000
MAX_CHART_BYTES = 2_500_000
MAX_BLOB_JSON = 3_600_000
MAX_MANIFEST_BYTES = 12_000
# The source runner may need to launch Playwright before producing the
# chart; allow it to finish before the 403 runner gives up on the bundle.
POLL_SECONDS = (0,) + (5,) * 12  # capped at 60 seconds
_WRITE_DELAYS = (0, 1, 2)
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _manifest_path(source_id: int) -> str:
    if type(source_id) is not int or not 1 <= source_id < 2**31:
        raise ValueError("INVALID_BUNDLE_SOURCE_ID")
    return f"{PREFIX}/{source_id}.json"


def _http_json(path: str, token: str, *, method: str = "GET",
               payload: dict | None = None, max_bytes: int = 20000):
    if not token:
        raise ValueError("NO_GITHUB_TOKEN")
    url = "https://api.github.com/repos/" + REPOSITORY + "/" + path
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(
        url, headers=headers, method=method,
        data=(json.dumps(payload, separators=(",", ":")).encode()
              if payload is not None else None))
    with request.urlopen(req, timeout=20) as resp:
        if method == "GET" and resp.status != 200:
            return None
        if method == "POST" and resp.status != 201:
            return None
        if method == "PUT" and resp.status not in (200, 201):
            return None
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        return json.loads(data)


def _canonical_title(page_title: str | None) -> str | None:
    """Lowchart HTML <title> appends current price and site label."""
    if not isinstance(page_title, str):
        return None
    return re.split(r"\s+-\s+", page_title.strip(), maxsplit=1)[0].strip()


def _photo_quality(data: bytes, extension: str) -> bool:
    if (extension not in _MIME or not isinstance(data, bytes)
            or not 1000 <= len(data) <= MAX_PHOTO_BYTES):
        return False
    signature = (
        data.startswith(b"\xff\xd8\xff") if extension == ".jpg"
        else data.startswith(b"\x89PNG\r\n\x1a\n") if extension == ".png"
        else data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    )
    if not signature:
        return False
    try:
        from PIL import Image
        with Image.open(BytesIO(data)) as photo:
            expected = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[extension[1:]]
            if photo.format != expected:
                return False
            w, h = photo.size
            if not (100 <= w <= 6000 and 100 <= h <= 6000
                    and w*h <= 30_000_000):
                return False
            photo.load()
        return True
    except (OSError, ValueError, TypeError, ImportError, KeyError):
        return False


def _file_meta(data: bytes, extension: str) -> dict:
    return {
        "git_blob_sha": None,
        "sha256": sha256(data).hexdigest(),
        "bytes": len(data),
        "extension": extension,
        "mime": _MIME[extension],
    }


def _git_blob_write(data: bytes, token: str) -> str | None:
    if not 1000 <= len(data) <= MAX_PHOTO_BYTES:
        return None
    obj = _http_json("git/blobs", token, method="POST", payload={
        "content": base64.b64encode(data).decode("ascii"),
        "encoding": "base64",
    }, max_bytes=3000)
    sha = obj.get("sha") if isinstance(obj, dict) else None
    return sha if isinstance(sha, str) and _SHA_RE.fullmatch(sha) else None


def _git_blob_read(meta: dict, token: str) -> bytes | None:
    if not isinstance(meta, dict):
        return None
    sha, digest, size, extension = (
        meta.get("git_blob_sha"), meta.get("sha256"),
        meta.get("bytes"), meta.get("extension"))
    if (not isinstance(sha, str) or not _SHA_RE.fullmatch(sha)
            or not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest)
            or type(size) is not int or not 1000 <= size <= MAX_PHOTO_BYTES
            or extension not in _MIME or meta.get("mime") != _MIME[extension]):
        return None
    obj = _http_json("git/blobs/" + sha, token,
                     max_bytes=MAX_BLOB_JSON)
    if (not isinstance(obj, dict) or obj.get("sha") != sha
            or obj.get("encoding") != "base64"
            or obj.get("size") != size
            or not isinstance(obj.get("content"), str)):
        return None
    try:
        data = base64.b64decode(obj["content"].replace("\n", ""),
                                validate=True)
    except (ValueError, TypeError):
        return None
    if len(data) != size or sha256(data).hexdigest() != digest:
        return None
    return data


def _manifest_read(source_id: int, token: str) -> dict | None:
    path = "contents/" + _manifest_path(source_id) + "?ref=" + CACHE_BRANCH
    try:
        obj = _http_json(path, token, max_bytes=MAX_MANIFEST_BYTES * 2)
        if (not isinstance(obj, dict) or obj.get("type") != "file"
                or not isinstance(obj.get("content"), str)):
            return None
        raw = base64.b64decode(obj["content"].replace("\n", ""),
                               validate=True)
        if not 0 < len(raw) <= MAX_MANIFEST_BYTES:
            return None
        return json.loads(raw.decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code != 404:
            print("LOWCHART_BUNDLE_READ_HTTP source_id="
                  + str(source_id) + " status=" + str(exc.code))
    except (error.URLError, OSError, ValueError, TypeError, UnicodeError):
        print("LOWCHART_BUNDLE_READ_FAILED source_id=" + str(source_id))
    return None


def _manifest_write(source_id: int, manifest: dict, token: str) -> bool:
    data = json.dumps(manifest, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_MANIFEST_BYTES:
        return False
    path = "contents/" + _manifest_path(source_id)
    payload = {
        "message": f"state: immutable Lowchart verified photo/chart for {source_id} [skip ci]",
        "branch": CACHE_BRANCH,
        "content": base64.b64encode(data).decode("ascii"),
    }
    for n, delay in enumerate(_WRITE_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            obj = _http_json(path, token, method="PUT",
                             payload=payload, max_bytes=5000)
            if obj is not None:
                print("LOWCHART_BUNDLE_STORED source_id=" + str(source_id))
                return True
        except error.HTTPError as exc:
            if exc.code in (409, 422):
                # Content-addressed assets are immutable. Another writer may
                # already have committed a complete manifest for this source.
                existing = _manifest_read(source_id, token)
                if existing is not None:
                    if existing == manifest:
                        return True
                    print("LOWCHART_BUNDLE_IMMUTABLE_CONFLICT " + str(source_id))
                    return False
                continue
            print("LOWCHART_BUNDLE_WRITE_HTTP source_id="
                  + str(source_id) + " status=" + str(exc.code))
            return False
        except (error.URLError, OSError):
            if _manifest_read(source_id, token) == manifest:
                return True
    print("LOWCHART_BUNDLE_WRITE_EXHAUSTED source_id=" + str(source_id))
    return False


def _manifest_valid(manifest: dict, source_id: int, pid: str,
                    iid: str, *, now: datetime | None = None) -> bool:
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA
            or manifest.get("source_id") != source_id
            or manifest.get("product_id") != pid
            or manifest.get("item_id") != iid):
        return False
    title = manifest.get("title")
    if not isinstance(title, str) or not 4 <= len(title) <= 220:
        return False
    if not is_coupang_cdn_url(manifest.get("image_url")):
        return False
    if manifest.get("source_url") != build_url(pid, iid):
        return False
    historical = manifest.get("price_record")
    price = verified_history(historical, source_id, pid, iid, now=now)
    if price is None or _canonical_title(price.get("page_title")) != title:
        return False
    photo = manifest.get("photo")
    if (not isinstance(photo, dict) or photo.get("extension") not in _MIME
            or not isinstance(photo.get("git_blob_sha"), str)
            or not _SHA_RE.fullmatch(photo["git_blob_sha"])
            or not isinstance(photo.get("sha256"), str)
            or not _DIGEST_RE.fullmatch(photo["sha256"])):
        return False
    chart = manifest.get("chart")
    if chart is not None and (
        not isinstance(chart, dict) or chart.get("extension") != ".png"
        or not isinstance(chart.get("git_blob_sha"), str)
        or not _SHA_RE.fullmatch(chart["git_blob_sha"])
    ):
        return False
    return True


def publish_bundle(source_id: int, identity: dict,
                   history: dict, title: str, image_url: str,
                   photo_path: Path, chart_path: Path | None = None,
                   *, token: str | None = None,
                   blob_writer=None, manifest_writer=None) -> bool:
    """Producer: a FULLY verified exact-item Lowchart page, no fallback title."""
    token = token if token is not None else os.getenv("GH_STATE_TOKEN", "")
    if not token:
        return False
    try:
        pid, iid = identity["product_id"], identity["item_id"]
        proof = create_record(source_id, history, title)
        if (proof is None or title != _canonical_title(history.get("page_title"))
                or not is_coupang_cdn_url(image_url)
                or not isinstance(photo_path, Path)
                or not photo_path.is_file()
                or photo_path.suffix.lower() not in _MIME
                or photo_path.stat().st_size > MAX_PHOTO_BYTES):
            return False
        photo = photo_path.read_bytes()
        if not _photo_quality(photo, photo_path.suffix.lower()):
            return False
        writer = blob_writer or _git_blob_write
        pmeta = _file_meta(photo, photo_path.suffix.lower())
        pmeta["git_blob_sha"] = writer(photo, token)
        if pmeta["git_blob_sha"] is None:
            return False
        cmeta = None
        if (isinstance(chart_path, Path) and chart_path.is_file()
                and chart_path.stat().st_size <= MAX_CHART_BYTES):
            chart = chart_path.read_bytes()
            if _inspect_native_png(chart) is not None:
                cmeta = _file_meta(chart, ".png")
                cmeta["git_blob_sha"] = writer(chart, token)
                if cmeta["git_blob_sha"] is None:
                    cmeta = None
        manifest = {
            "schema": SCHEMA, "source_id": source_id,
            "product_id": pid, "item_id": iid,
            "source_url": build_url(pid, iid), "title": title,
            "image_url": image_url, "price_record": proof,
            "photo": pmeta, "chart": cmeta,
        }
        sender = manifest_writer or _manifest_write
        return bool(sender(source_id, manifest, token))
    except (OSError, ValueError, TypeError, KeyError,
            error.HTTPError, error.URLError) as exc:
        print("LOWCHART_BUNDLE_STORE_ERROR source_id=" + str(source_id)
              + " error_type=" + type(exc).__name__)
        return False


def recover_bundle(source_id: int, identity: dict, folder: Path,
                   *, token: str | None = None, reader=None,
                   blob_reader=None, sleeper=None) -> dict | None:
    """Consumer: read actual media bytes + exact price and restore local files.

    No alias-based photo fallback; bundle must be valid and photo fully
    downloaded/verified before a caller may label it exact-item.
    Native chart is optional; if unavailable publisher can still try live.
    """
    token = token if token is not None else os.getenv("GH_STATE_TOKEN", "")
    if not token:
        return None
    try:
        source = _manifest_path(source_id)
        pid, iid = identity["product_id"], identity["item_id"]
        build_url(pid, iid)
        if identity.get("status") != "EXACT_ITEM_IDENTIFIED":
            return None
    except (ValueError, KeyError, TypeError):
        return None
    fetch = reader or _manifest_read
    get_blob = blob_reader or _git_blob_read
    sleeper = sleeper or time.sleep
    for delay in POLL_SECONDS:
        if delay:
            sleeper(delay)
        try:
            manifest = fetch(source_id, token)
            if not _manifest_valid(manifest, source_id, pid, iid):
                continue
            raw = get_blob(manifest["photo"], token)
            ext = manifest["photo"]["extension"]
            if not _photo_quality(raw, ext):
                continue
            # File writes are the only mutation; never accept path from remote.
            folder.mkdir(parents=True, exist_ok=True)
            photo_path = folder / f"jirum_{source_id}_lowchart_photo{ext}"
            photo_path.write_bytes(raw)
            chart_path = None
            if isinstance(manifest.get("chart"), dict):
                native = get_blob(manifest["chart"], token)
                if _inspect_native_png(native) is not None:
                    chart_path = folder / f"jirum_{source_id}_lowchart_chart.png"
                    chart_path.write_bytes(native)
            print("LOWCHART_BUNDLE_RECOVERED " + json.dumps({
                "source_id": source_id, "product_id": pid,
                "item_id": iid, "photo_bytes": len(raw),
                "chart_shared": chart_path is not None,
            }))
            return {
                "title": manifest["title"],
                "image_url": manifest["image_url"],
                "photo_path": photo_path,
                "chart_path": chart_path,
                "history": dict(manifest["price_record"]["history"]),
            }
        except (error.HTTPError, error.URLError, OSError, TypeError,
                ValueError, KeyError):
            continue
    print("LOWCHART_BUNDLE_UNAVAILABLE source_id=" + str(source_id))
    return None



def prepare_native_chart(identity: dict, folder: Path,
                         source_id: int) -> Path | None:
    """Prepare original chart BEFORE final bundle publication, if enabled.

    Uses the existing verified public item+native-PNG gate and an unauthenticated
    browser. Failure is optional and never triggers an unverified screenshot.
    """
    if os.getenv("JIRUM_LOWCHART_CHART_IMAGE_ENABLED") != "true":
        return None
    try:
        from playwright.sync_api import sync_playwright
        from gh_only.lowchart_chart_capture import capture_chart_image
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                return capture_chart_image(browser, {
                    "product_id": identity["product_id"],
                    "item_id": identity["item_id"],
                    "verified_exact_item": True,
                    "source_url": build_url(
                        identity["product_id"], identity["item_id"]),
                }, folder, source_id)
            finally:
                browser.close()
    except Exception as exc:
        print("LOWCHART_BUNDLE_CHART_PREPARE_FAILED source_id="
              + str(source_id) + " error_type=" + type(exc).__name__)
        return None



def local_verified_chart(identity: dict | None, folder: Path,
                         source_id: int) -> Path | None:
    """Reuse a correctly named, fully verified native chart if prepared.

    Caller already passes the exact verified chart_identity for the same
    source; never accept an arbitrary caller-provided filename or image.
    """
    try:
        if (not isinstance(identity, dict)
                or identity.get("verified_exact_item") is not True
                or identity.get("source_url") !=
                   build_url(identity["product_id"], identity["item_id"])
                or type(source_id) is not int or source_id <= 0):
            return None
        path = folder / f"jirum_{source_id}_lowchart_chart.png"
        if not path.is_file() or path.stat().st_size > MAX_CHART_BYTES:
            return None
        return path if _inspect_native_png(path.read_bytes()) else None
    except (OSError, ValueError, TypeError, KeyError):
        return None
