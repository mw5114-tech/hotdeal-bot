"""Read-only proof that a publicly indexed Coupang CDN image can be downloaded in GitHub.

This verifies transport / image bytes ONLY. The hardcoded URL was found on a
DIFFERENT Coupang listing for the same-looking 1kg package. It is not proven
to be the representative image of productId=6221369038/itemId=12453073752.
Never treat this probe as authorization to repost a retailer's artwork.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from urllib import request, error
from urllib.parse import urlsplit
import hashlib
import json

URL = "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/retail/images/87060605900171-f042e02a-7905-47b9-8c3f-24f261864cf5.jpg"
MAX_IMAGE_BYTES = 3_000_000

def is_cdn_image_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.hostname == "thumbnail.coupangcdn.com"
                and p.port in (None, 443) and not p.username and not p.password
                and p.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                and len(url) <= 500)
    except ValueError:
        return False

def image_signature(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None

def fetch_one(url, output: Path, opener=None):
    if not is_cdn_image_url(url):
        raise ValueError("INVALID_CDN_IMAGE_URL")
    req = request.Request(url, headers={"Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8"})
    get = opener or request.urlopen
    try:
        with get(req, timeout=20) as response:
            code = response.status
            host = urlsplit(response.geturl()).hostname
            content_type = response.headers.get("Content-Type", "").split(";",1)[0]
            data = response.read(MAX_IMAGE_BYTES + 1)
    except error.HTTPError as err:
        return {"status":"HTTP_ERROR","http_code":err.code,"image_saved":False}
    except Exception as exc:
        return {"status":"NETWORK_ERROR","error_type":type(exc).__name__,
                "image_saved":False}

    kind = image_signature(data)
    if (code != 200 or host != "thumbnail.coupangcdn.com" or
            len(data) > MAX_IMAGE_BYTES or not kind or
            content_type not in ("image/jpeg", "image/png", "image/webp")):
        return {"status":"NOT_VALID_IMAGE","http_code":code,"image_saved":False,
                "type":content_type,"bytes":len(data),"file_signature":kind}
    output.write_bytes(data)
    return {"status":"IMAGE_BYTES_OK","http_code":code,
            "image_saved":True,"type":content_type,
            "format":kind,"bytes":len(data),
            "sha256":hashlib.sha256(data).hexdigest(),
            "exact_product_image_verified":False,
            "rights_to_republish_verified":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--report",default="coupang_cdn_image_report.json")
    p.add_argument("--image",default="coupang_candidate.jpg")
    args=p.parse_args()
    try:
        report=fetch_one(URL,Path(args.image))
    except Exception as e:
        report={"status":"PROBE_ERROR","type":type(e).__name__,"image_saved":False}
    report["source"]="public_index_of_other_coupang_listing"
    report["target_product_id"]="6221369038"
    report["target_item_id"]="12453073752"
    report["exact_item_match"]=False
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("CDN_IMAGE_TEST "+json.dumps(report,ensure_ascii=False))

if __name__=="__main__":
    main()
