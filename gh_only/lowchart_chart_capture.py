"""Optional read-only Lowchart chart screenshot for an exact Coupang item.

Never fetch other SKUs or scrape private APIs. A failed chart is not a reason to
drop an otherwise verified hot deal; only successfully rendered chart images
are passed to an editor. Reuse the publisher's Chromium, but open a fresh
context with no Naver/Daangn login cookies.
"""
from __future__ import annotations

import os
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


def capture_chart_image(browser, identity: dict | None, folder: Path,
                        source_id: int) -> Path | None:
    if os.getenv("JIRUM_LOWCHART_CHART_IMAGE_ENABLED") != "true":
        return None
    keys = _exact_identity(identity)
    if keys is None or type(source_id) is not int or source_id <= 0:
        print("LOWCHART_CHART_SKIPPED_INVALID_EXACT_ITEM")
        return None
    url = build_url(*keys)
    ctx = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ctx = browser.new_context(
            locale="ko-KR", viewport={"width": 1180, "height": 950},
            device_scale_factor=2,
        )
        page = ctx.new_page()
        response = page.goto(url, wait_until="domcontentloaded", timeout=16000)
        if (response is None or response.status != 200
                or page.url != url):
            print("LOWCHART_CHART_SKIPPED_UNEXPECTED_PAGE")
            return None
        # Wait only for the public chart's native visible share control.
        page.get_by_role("button", name="차트 퍼가기").wait_for(
            state="visible", timeout=8000
        )
        page.wait_for_timeout(600)
        bounds = chart_bounds(page)
        if bounds is None:
            print("LOWCHART_CHART_SKIPPED_NOT_RENDERED")
            return None
        path = folder / f"jirum_{source_id}_lowchart_chart.jpg"
        page.screenshot(path=str(path), type="jpeg", quality=78,
                        clip=bounds, timeout=10000)
        if not (6000 <= path.stat().st_size <= MAX_CHART_BYTES):
            path.unlink(missing_ok=True)
            print("LOWCHART_CHART_SKIPPED_IMAGE_INVALID")
            return None
        print("LOWCHART_CHART_CAPTURED " + str({
            "source_id": source_id, "bytes": path.stat().st_size,
            "width": bounds["width"], "height": bounds["height"],
            "exact_item": True,
        }))
        return path
    except Exception as exc:
        # Never log arbitrary page text, cookie state or URL query strings.
        print("LOWCHART_CHART_SKIPPED_" + type(exc).__name__.upper())
        return None
    finally:
        if ctx is not None:
            ctx.close()
