"""One stable, machine-readable Telegram message format for both sources."""


def format_hotdeal_message(name: str, url: str) -> str:
    """Preserve product title and original URL; keep field values one line each."""
    clean_name = " ".join(name.split())[:180]
    return f"🆕 [HOTDEAL]\n\n상품명: {clean_name}\n\n상품링크: {url}"
