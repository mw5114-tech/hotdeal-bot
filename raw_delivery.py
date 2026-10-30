"""Single-destination Bot API adapter, preserving legacy target configuration."""
import json
import os
import socket
from urllib import error, request
from urllib.parse import urlencode
from telegram_delivery import target_chats


class DeliveryError(Exception):
    def __init__(self, status, reason, delay=0):
        super().__init__(reason)
        self.status, self.reason, self.delay = status, reason, delay


def targets():
    result = target_chats()
    if [label for label, _ in result] != ["personal", "group"]:
        raise RuntimeError("BOTH_DESTINATIONS_REQUIRED")
    if not result[0][1].isdigit() or int(result[0][1]) <= 0:
        raise RuntimeError("PERSONAL_CHAT_ID_INVALID")
    return result


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DeliveryError("uncertain", "UNEXPECTED_REDIRECT")


class Bot:
    def __init__(self, token=None, opener=None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self.token:
            raise RuntimeError("BOT_TOKEN_REQUIRED")
        self.opener = opener or request.build_opener(NoRedirect()).open

    def call(self, method, values):
        if method not in {"getMe", "getChat", "sendMessage"}:
            raise ValueError("METHOD_NOT_ALLOWED")
        req = request.Request("https://api.telegram.org/bot" + self.token + "/" + method,
                              data=urlencode(values).encode(), method="POST")
        try:
            try:
                with self.opener(req, timeout=25) as response:
                    body = response.read(1000001)
            except error.HTTPError as exc:
                # Read a bounded error envelope, never expose description/URL.
                body = exc.read(1000001)
                if exc.code >= 500:
                    raise DeliveryError("uncertain", "TELEGRAM_SERVER_ERROR") from None
            if len(body) > 1000000:
                raise DeliveryError("uncertain", "RESPONSE_TOO_LARGE")
            obj = json.loads(body)
            if not isinstance(obj, dict):
                raise DeliveryError("uncertain", "INVALID_API_RESPONSE")
            if obj.get("ok") is not True:
                if obj.get("error_code") == 429:
                    wait = obj.get("parameters", {}).get("retry_after", 60)
                    wait = wait if type(wait) is int and 0 < wait <= 86400 else 60
                    raise DeliveryError("retry", "RATE_LIMITED", wait + 1)
                raise DeliveryError("blocked", "API_REJECTED")
            return obj["result"]
        except DeliveryError:
            raise
        except error.URLError as exc:
            if isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError)):
                raise DeliveryError("retry", "CONNECT_FAILED", 30) from None
            raise DeliveryError("uncertain", "NETWORK_RESULT_UNKNOWN") from None
        except Exception:
            raise DeliveryError("uncertain", "SEND_RESULT_UNKNOWN") from None

    def check_targets(self, configured):
        me = self.call("getMe", {})
        if me.get("is_bot") is not True or me.get("first_name") != "이 가격 못 참지 BOT":
            raise RuntimeError("BOT_IDENTITY_INVALID")
        for label, chat in configured:
            result = self.call("getChat", {"chat_id": chat})
            if str(result.get("id")) != chat:
                raise RuntimeError("DESTINATION_ID_MISMATCH")
            if label == "group" and (result.get("title") != "핫딜 MUSE용" or result.get("type") not in {"group", "supergroup"}):
                raise RuntimeError("GROUP_IDENTITY_MISMATCH")
            if label == "personal" and result.get("type") != "private":
                raise RuntimeError("PERSONAL_IDENTITY_MISMATCH")

    def send(self, chat, payload):
        try:
            original = json.loads(payload)
            text, entities = original["text"], original["entities"]
            if not isinstance(text, str) or not isinstance(entities, list) or not text:
                raise ValueError()
        except Exception:
            raise DeliveryError("blocked", "INVALID_ORIGINAL_PAYLOAD") from None
        if len(text) > 4096:
            # Do not truncate or split: user requires one unchanged message.
            raise DeliveryError("blocked", "ORIGINAL_EXCEEDS_BOT_MESSAGE_LIMIT")
        result = self.call("sendMessage", {"chat_id": chat, "text": text,
            "entities": json.dumps(entities, ensure_ascii=False),
            "disable_web_page_preview": "true"})
        if type(result.get("message_id")) is not int:
            raise DeliveryError("uncertain", "MESSAGE_RECEIPT_MISSING")
        # Bot API echoes accepted message text. Do not log the text on mismatch.
        if result.get("text") != text:
            raise DeliveryError("uncertain", "RETURNED_TEXT_MISMATCH")
        return result["message_id"]
