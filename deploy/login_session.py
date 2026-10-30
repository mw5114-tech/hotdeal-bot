"""Optional interactive login on the server, performed by the account owner.
Run via systemd-run --pty with EnvironmentFile, while the watcher is stopped.
Never supply an OTP, phone number, or password as a command line argument.
"""
import asyncio
from getpass import getpass
import logging
import os
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


async def main():
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    path = Path(os.environ["TELETHON_SESSION_PATH"])
    if not path.is_absolute():
        raise RuntimeError("Absolute session path required")
    client = TelegramClient(str(path), int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"])
    client.session.save_entities = False
    try:
        await client.connect()
        if not await client.is_user_authorized():
            phone = getpass("Telegram phone number (hidden): ")
            await client.send_code_request(phone)
            try:
                await client.sign_in(phone=phone, code=getpass("Telegram login code (hidden): "))
            except SessionPasswordNeededError:
                await client.sign_in(password=getpass("Telegram 2FA password (hidden): "))
        channel = await client.get_entity("jirum")
        if getattr(channel, "username", "").lower() != "jirum":
            raise RuntimeError("Unexpected source")
        print("Session authorized; no chat history collected and no message sent.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("Login did not complete; credential details hidden.")
        raise SystemExit(1)
