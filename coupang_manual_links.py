"""Optional manual Coupang Partners URL substitutions.

This does NOT generate affiliate links. Before the Partners API is available,
only links manually created for the EXACT SAME product and option may be mapped.
An unmapped or non-Coupang source is never modified.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

CONFIG = Path(__file__).resolve().parent / "config" / "coupang_manual_links.json"


def _coupang_source(url):
    if not isinstance(url, str):
        return False
    try:
        p = urlsplit(url)
        return (
            p.scheme == "https"
            and p.username is None and p.password is None
            and (
                (p.hostname or "").lower() == "link.coupang.com"
                or (p.hostname or "").lower() in ("www.coupang.com", "coupang.com")
            )
        )
    except ValueError:
        return False


def _personal_affiliate_link(url):
    if not isinstance(url, str):
        return False
    try:
        p = urlsplit(url)
        return (
            p.scheme == "https"
            and (p.hostname or "").lower() == "link.coupang.com"
            and p.path.startswith("/a/")
            and len(p.path) > 3
            and p.username is None and p.password is None
            and not p.fragment
        )
    except ValueError:
        return False


def resolve_manual_link(source_url):
    """Return the mapped personal link only on a fully exact source URL match."""
    if not _coupang_source(source_url) or not CONFIG.exists():
        return source_url
    try:
        settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Invalid manual Coupang mapping configuration") from exc
    if not isinstance(settings, dict) or not isinstance(settings.get("mappings"), dict):
        raise RuntimeError("Invalid manual Coupang mapping structure")
    mappings = settings["mappings"]
    for source, replacement in mappings.items():
        if not _coupang_source(source) or not _personal_affiliate_link(replacement):
            raise RuntimeError("Invalid Coupang source or personal affiliate link mapping")
    return mappings.get(source_url, source_url)
