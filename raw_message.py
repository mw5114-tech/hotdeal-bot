"""Lossless source text and MTProto -> Bot API formatting entities only."""
import json

ENTITY_TYPES = {
    "MessageEntityMention": "mention", "MessageEntityHashtag": "hashtag",
    "MessageEntityBotCommand": "bot_command", "MessageEntityUrl": "url",
    "MessageEntityEmail": "email", "MessageEntityBold": "bold",
    "MessageEntityItalic": "italic", "MessageEntityCode": "code",
    "MessageEntityPre": "pre", "MessageEntityTextUrl": "text_link",
    "MessageEntityPhone": "phone_number", "MessageEntityCashtag": "cashtag",
    "MessageEntityUnderline": "underline", "MessageEntityStrike": "strikethrough",
    "MessageEntitySpoiler": "spoiler", "MessageEntityCustomEmoji": "custom_emoji",
    "MessageEntityBlockquote": "blockquote",
}


def bot_entities(message):
    output = []
    # Offsets/lengths in both APIs use UTF-16. Copy, do not recalculate.
    for source in message.entities or []:
        kind = ENTITY_TYPES.get(type(source).__name__)
        if not kind:
            # Unsupported metadata is omitted without altering a text character.
            continue
        item = {"type": kind, "offset": source.offset, "length": source.length}
        if kind == "text_link":
            item["url"] = source.url
        if kind == "pre":
            item["language"] = source.language or ""
        if kind == "custom_emoji":
            item["custom_emoji_id"] = str(source.document_id)
        if kind == "blockquote" and getattr(source, "collapsed", False):
            item["type"] = "expandable_blockquote"
        output.append(item)
    return output


def extract(message):
    # raw_text is Telegram's exact unformatted text, including caption text.
    # Never strip, unescape, normalize Unicode, summarize, or append a URL.
    text = message.raw_text
    return {"id": message.id, "text": text if text is not None else "", "entities": bot_entities(message)}


def format_parts(post, test=False):
    # Single sendMessage per destination. Even manual tests add NO marker.
    if post["text"] == "":
        return []
    return [json.dumps({"text": post["text"], "entities": post["entities"]}, ensure_ascii=False, separators=(",", ":"))]
