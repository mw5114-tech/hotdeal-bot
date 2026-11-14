"""Optional read-only Lowchart chart screenshot for an exact Coupang item.

Never fetch other SKUs or scrape private APIs. A failed chart is not a reason to
drop an otherwise verified hot deal; only successfully rendered chart images
are passed to an editor. Reuse the publisher's Chromium, but open a fresh
context with no Naver/Daangn login cookies.
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
import re

from gh_only.lowchart_price_reference import build_url

MAX_CHART_BYTES = 2_500_000


def _exact_identity(identity: dict | None) -> tuple[str, str] | None:
    if not isinstance(identity, dict):
        return None
    p, i = identity.get("product_id"), identity.get("item_id")
    if (isinstance(p, str) and isinstance(i, str)
            and re.fullmatch(r"[0-9]{5,18}", p)
            and re.fullmatch(r"[0-9]{5,18}", i)):
        return p, i
    return None


def chart_bounds(page) -> dict | None:
    """Clip only the chart section between its visible heading and share UI.

    This works for image, SVG, Canvas and HTML-rendered price charts without
    guessing a private image API, drawing invented data, or including product
    recommendations below the chart.
    """
    return page.evaluate(r"""() => {
      const visible = el => !!el && !!el.getClientRects().length;
      const all = Array.from(document.querySelectorAll('h1,h2,h3,h4,button'));
      const heading = all.find(e => visible(e) &&
          /^지금\s*사도\s*될까\??$/.test((e.innerText || '').trim()));
      const share = all.find(e => e.tagName === 'BUTTON' && visible(e) &&
          (e.innerText || '').includes('차트 퍼가기'));
      if (!heading || !share) return null;
      const h = heading.getBoundingClientRect();
      const b = share.getBoundingClientRect();
      if (b.top <= h.bottom + 200 || b.top - h.top > 1300) return null;
      // Narrow the horizontal clip to the smallest content container common
      // to the chart heading and the sharing control.
      let section = null;
      for (let p = heading.parentElement; p; p = p.parentElement) {
        if (!p.contains(share)) continue;
        const r = p.getBoundingClientRect();
        if (r.width >= 520 && r.width <= 1150 && r.height <= 1400) {
          section = r;
          break;
        }
      }
      const x = Math.max(0, Math.floor(
          (section ? section.left : Math.min(h.left, b.left)) - 8 + scrollX));
      const right = Math.min(document.documentElement.scrollWidth,
          Math.ceil((section ? section.right : Math.max(h.right, b.right) + 650)
              + 8 + scrollX));
      const y = Math.max(0, Math.floor(h.top - 8 + scrollY));
      const bottom = Math.ceil(b.bottom + 8 + scrollY);
      const width = right - x, height = bottom - y;
      if (width < 520 || width > 1180 || height < 240 || height > 1350)
        return null;
      // Lowchart currently renders its native watermarked chart as an
      // image. Prefer that exact graphic: surrounding HTML controls may
      // have missing CJK fonts in a headless GitHub runner.
      const visuals = Array.from(document.querySelectorAll('img,svg,canvas'))
        .map(el => ({el, r: el.getBoundingClientRect()}))
        .filter(({r}) => r.width >= 390 && r.height >= 170 &&
                 r.top > h.bottom - 20 && r.bottom < b.top + 35);
      if (!visuals.length) return null;
      const nativeImages = visuals.filter(({el}) => el.tagName === 'IMG' &&
          el.complete && el.naturalWidth >= 500 && el.naturalHeight >= 175);
      if (nativeImages.length) {
        nativeImages.sort((a,b) =>
            b.r.width*b.r.height - a.r.width*a.r.height);
        const r = nativeImages[0].r;
        return {x: Math.max(0,Math.floor(r.left + scrollX)),
          y: Math.max(0,Math.floor(r.top + scrollY)),
          width: Math.ceil(r.width), height: Math.ceil(r.height)};
      }
      // SVG/Canvas fallback: include the heading and the public source
      // label rather than guessing arbitrary pixel positions.
      return {x,y,width,height};
    }""")


def _native_chart_url(product_id: str, item_id: str) -> str:
    """Only the exact chart asset displayed by the public product page."""
    build_url(product_id, item_id)  # Validate digit-only exact item.
    return f"https://chart.lowchart.com/{product_id}-{item_id}.png"


def _inspect_native_png(data: bytes) -> dict | None:
    """Reject blanks, placeholder loading art, clipped or pale renderings.

    We inspect the ORIGINAL full-resolution image rather than an HTML/CSS
    screenshot. The appearance gate intentionally fails closed if unable to
    establish a readable price chart. Does not alter or redraw chart data.
    """
    if not isinstance(data, bytes) or not (
        18000 <= len(data) <= MAX_CHART_BYTES
    ) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    try:
        from PIL import Image, ImageStat
        with Image.open(BytesIO(data)) as im:
            if im.format != "PNG":
                return None
            width, height = im.size
            if not (1100 <= width <= 6500 and 290 <= height <= 2200
                    and 2.0 <= width / height <= 5.5):
                return None
            # A fully decoded image catches truncated/corrupt PNG streams.
            im.load()
            # Calculate visual contrast from uniformly downsampled pixels,
            # rejecting skeleton/transparent/washed-out chart graphics.
            if im.mode in ("RGBA", "LA", "P") and "transparency" in im.info:
                rgba = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, "white")
                bg.alpha_composite(rgba)
                mono = bg.convert("L")
            else:
                mono = im.convert("L")
            small = mono.resize((240, 75))
            stat = ImageStat.Stat(small)
            hist = small.histogram()
            dark_fraction = sum(hist[:150]) / sum(hist)
            if stat.stddev[0] < 17 or dark_fraction < 0.008:
                return None
            return {
                "width": width,
                "height": height,
                "grayscale_std": round(stat.stddev[0], 1),
                "dark_fraction": round(dark_fraction, 4),
            }
    except (OSError, ValueError, ImportError, TypeError):
        return None


def capture_chart_image(browser, identity: dict | None, folder: Path,
                        source_id: int) -> Path | None:
    """Download exact-item native PNG, never screenshot page/HTML placeholders.

    The public native source already contains Korean text rendered by
    Lowchart. Browser screenshot fallbacks caused intermittent low-contrast
    skeleton snapshots, missing Korean fonts and JPEG blur. Never downgrade
    to HTML screenshot if the native graphic is not ready or fails quality.
    """
    if os.getenv("JIRUM_LOWCHART_CHART_IMAGE_ENABLED") != "true":
        return None
    keys = _exact_identity(identity)
    if keys is None or type(source_id) is not int or source_id <= 0:
        print("LOWCHART_CHART_SKIPPED_INVALID_EXACT_ITEM")
        return None
    url = build_url(*keys)
    native_url = _native_chart_url(*keys)
    ctx = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"jirum_{source_id}_lowchart_chart.png"
        # Never inherit a prior run's local artifact on a failed new attempt.
        path.unlink(missing_ok=True)
        ctx = browser.new_context(
            locale="ko-KR", viewport={"width": 1180, "height": 950},
            device_scale_factor=1,
        )
        page = ctx.new_page()
        response = page.goto(url, wait_until="domcontentloaded", timeout=19000)
        if response is None or response.status != 200 or page.url != url:
            print("LOWCHART_NATIVE_CHART_SKIPPED_UNEXPECTED_PAGE")
            return None
        share = page.get_by_role("button", name="차트 퍼가기")
        share.wait_for(state="visible", timeout=10000)
        # Lazy native <img> loads on entering view. Do not capture skeleton.
        share.scroll_into_view_if_needed(timeout=6000)
        try:
            page.wait_for_function(
                """expected => [...document.images].some(img =>
                   (img.currentSrc || img.src) === expected &&
                   (img.alt || '').includes('가격 변동 차트') &&
                   img.complete &&
                   img.naturalWidth >= 1100 && img.naturalHeight >= 290
                )""",
                arg=native_url,
                timeout=11000,
            )
        except Exception:
            print("LOWCHART_NATIVE_CHART_SKIPPED_IMAGE_NOT_READY")
            return None
        # Public image linked on the exact-item product page only, not the
        # recommendation grid, another option or an unrelated search result.
        result = ctx.request.get(native_url, timeout=18000, max_redirects=0)
        if (result.status != 200 or result.url != native_url or
                not result.headers.get("content-type", "").lower().startswith("image/png")):
            print("LOWCHART_NATIVE_CHART_SKIPPED_SOURCE_INVALID")
            return None
        length = result.headers.get("content-length", "")
        if length.isdecimal() and int(length) > MAX_CHART_BYTES:
            print("LOWCHART_NATIVE_CHART_SKIPPED_TOO_LARGE")
            return None
        data = result.body()
        quality = _inspect_native_png(data)
        if quality is None:
            print("LOWCHART_NATIVE_CHART_SKIPPED_UNREADABLE_PNG")
            return None
        path.write_bytes(data)
        print("LOWCHART_NATIVE_CHART_VERIFIED " + str({
            "source_id": source_id,
            "product_id": keys[0], "item_id": keys[1],
            "bytes": len(data), "exact_item": True,
            **quality,
        }))
        return path
    except Exception as exc:
        # Never log arbitrary page text, cookie state or URL query strings.
        print("LOWCHART_NATIVE_CHART_SKIPPED_" + type(exc).__name__.upper())
        return None
    finally:
        if ctx is not None:
            ctx.close()
