"""Synthetic UI regression: no Coupang connection or real session."""
import re
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content('<h1>간편 링크 만들기</h1><input id="url"><button>링크 생성&#8203;</button>')
    assert page.locator("input#url").is_visible()
    assert page.get_by_role("button", name=re.compile(r"^링크\s*생성[\s\u200b]*$")).is_visible()
    page.set_content('<input id="url" hidden><button hidden>링크 생성&#8203;</button>')
    assert not page.locator("input#url").is_visible()
    assert not page.get_by_role("button", name=re.compile(r"^링크\s*생성[\s\u200b]*$")).is_visible()
    browser.close()
print("Visible current form accepted; hidden controls rejected. No real session used.")
