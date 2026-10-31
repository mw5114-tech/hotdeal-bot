"""Verify the exact Kakao OG:image URL the user inspected for a Jirum product.

This is an explicitly supplied, manually verified OG link: do not pretend it
was automatically discovered by GitHub or by Kakao's public image-search API.
No product-page visit, no login, no posting.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from gh_only.cdn_image_probe import fetch_one

KAKAO_OG_IMAGE = (
 "https://thumbnail.coupangcdn.com/thumbnails/remote/640x640ex/image/retail/images/"
 "87060605900171-f042e02a-7905-47b9-8c3f-24f261864cf5.jpg"
)
OLDER_492_IMAGE = (
 "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/retail/images/"
 "87060605900171-f042e02a-7905-47b9-8c3f-24f261864cf5.jpg"
)
TARGET_PRODUCT = "6221369038"
TARGET_ITEM = "12453073752"

def same_source_asset(url_a, url_b):
    """URL path identity only: does not establish the correct item or photo rights."""
    a, b = urlsplit(url_a), urlsplit(url_b)
    return (a.hostname == b.hostname == "thumbnail.coupangcdn.com"
            and a.path.split("/image/", 1)[-1] == b.path.split("/image/", 1)[-1]
            and "/image/" in a.path and "/image/" in b.path)

def main():
    location = Path("hotcake_og_640.jpg")
    result = fetch_one(KAKAO_OG_IMAGE, location)
    result.update({
        "schema": "github-manual-kakao-og-proof.v1",
        "source_url": "https://link.coupang.com/a/iiCmQxWdUG",
        "manual_kakao_og_title": "오뚜기 핫케이크 믹스, 1kg, 1개",
        "manual_kakao_og_url": "https://www.coupang.com/vp/products/6221369038",
        "target_product_id": TARGET_PRODUCT,
        "target_item_id": TARGET_ITEM,
        "og_image_url": KAKAO_OG_IMAGE,
        "same_underlying_asset_as_prior_492_test": same_source_asset(KAKAO_OG_IMAGE, OLDER_492_IMAGE),
        "manual_provenance": "user_kakao_og_management_screenshot",
        "automatically_discovered": False,
        "exact_item_id_proven_by_og_url": False,
        "safe_to_autopublish": False,
    })
    if location.is_file():
        try:
            from PIL import Image
            with Image.open(location) as im:
                result["image_dimensions_px"] = list(im.size)
                result["image_format_pillow"] = im.format
        except Exception as exc:
            result["image_dimensions_error"] = type(exc).__name__
    Path("hotcake_og_640_report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("KAKAO_OG_640_TEST "+json.dumps({
        "status": result["status"],
        "http_code": result.get("http_code"),
        "bytes": result.get("bytes",0),
        "dimensions":result.get("image_dimensions_px"),
        "same_asset":result["same_underlying_asset_as_prior_492_test"],
        "item_verified":False,
        "published":False
    },ensure_ascii=False))

if __name__=="__main__":
    main()
