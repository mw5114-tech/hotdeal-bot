"""Original-text-only @jirum NewMessage watcher. No web polling or Actions."""
import argparse
import asyncio
from contextlib import contextmanager, nullcontext
import json
import logging
import os
from pathlib import Path
import signal
import time
import uuid
from urllib.parse import urlsplit

from raw_message import extract, format_parts
from live_store import Store
from raw_delivery import Bot, DeliveryError, targets


def log(code, **values):
    # Only caller-selected IDs/counts/status, never exceptions/text/URLs/secrets.
    print(json.dumps({"event": code, **values}, ensure_ascii=True), flush=True)


def load_env(path):
    if not path:
        return
    # Literal KEY=value format only; no shell evaluation or interpolation.
    for line in Path(path).read_text("utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.replace("_", "").isalnum():
            raise RuntimeError("ENV_FILE_FORMAT_INVALID")
        os.environ[key] = value


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            raise RuntimeError("ANOTHER_PROCESS_RUNNING") from None
    else:
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("ANOTHER_PROCESS_RUNNING") from None
    try:
        yield
    finally:
        handle.close()


async def deliver_one(store, bot, scope="live"):
    row = store.claim(scope)
    if not row:
        return False
    try:
        receipt = await asyncio.to_thread(bot.send, row["chat"], row["text"])
    except DeliveryError as exc:
        store.finish(row, exc.status, reason=exc.reason, delay=exc.delay)
        log("DELIVERY_" + exc.status.upper(), post=row["post_id"], destination=row["destination"], part=row["part"], reason=exc.reason)
    except Exception:
        store.finish(row, "uncertain", reason="UNEXPECTED_SEND_RESULT")
        log("DELIVERY_UNCERTAIN", post=row["post_id"], destination=row["destination"], part=row["part"])
    else:
        store.finish(row, "sent", remote_id=receipt)
        log("DELIVERED", post=row["post_id"], destination=row["destination"], part=row["part"], receipt=receipt)
    return True


async def reconcile(client, channel, store, configured, wake):
    # This is MTProto gap repair, not the primary new-post detection mechanism.
    # Bounded pages; persist cursor only after durable enqueue in ID order.
    while True:
        count = 0
        async for message in client.iter_messages(channel, min_id=store.get("cursor"), limit=100, reverse=True):
            post = extract(message)
            store.put(post["id"], post["text"], format_parts(post), configured, scanned=True)
            count += 1
        wake.set()
        if count < 100:
            return


async def connected(configured):
    from telethon import TelegramClient
    session = Path(os.environ.get("TELETHON_SESSION_PATH", ""))
    if not session.is_absolute() or not session.is_file():
        raise RuntimeError("EXISTING_ABSOLUTE_SESSION_FILE_REQUIRED")
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    if api_id <= 0 or len(api_hash) != 32:
        raise RuntimeError("TELEGRAM_API_CREDENTIALS_REQUIRED")
    client = TelegramClient(str(session), api_id, api_hash, auto_reconnect=True,
                            connection_retries=5, retry_delay=3, sequential_updates=True)
    client.session.save_entities = False
    try:
        await asyncio.wait_for(client.connect(), timeout=45)
        if not await client.is_user_authorized():
            raise RuntimeError("SESSION_NOT_AUTHORIZED_LOGIN_MANUALLY")
        channel = await client.get_entity("jirum")
        if getattr(channel, "username", "").lower() != "jirum" or not getattr(channel, "broadcast", False):
            raise RuntimeError("SOURCE_CHANNEL_IDENTITY_MISMATCH")
        return client, channel
    except BaseException:
        await client.disconnect()
        raise


async def run_service(store, enabled):
    from telethon import events
    configured = targets()
    client, channel = await connected(configured)
    tasks = []
    stopped = asyncio.Event()
    sender_task = None
    wake = asyncio.Event()
    try:
        source = store.get("source_id")
        if source is not None and source != channel.id:
            raise RuntimeError("SOURCE_CHANNEL_CHANGED")
        with store.db:
            store.set("source_id", channel.id)
        if store.get("baseline") is None:
            latest = await client.get_messages(channel, limit=1)
            store.initialize(latest[0].id if latest else 0, configured)
            log("BASELINE_ONLY", latest=store.get("baseline"), historical_sends=0)
        else:
            store.initialize(store.get("baseline"), configured)
        store.recover()
        bot = Bot() if enabled else None
        if bot:
            await asyncio.to_thread(bot.check_targets, configured)
        fatal = asyncio.get_running_loop().create_future()

        async def on_message(event):
            try:
                post = extract(event.message)
                store.put(post["id"], post["text"], format_parts(post), configured)
                wake.set()
                log("SOURCE_EVENT", post=post["id"], edited=event.message.edit_date is not None)
            except Exception:
                if not fatal.done():
                    fatal.set_result("INGEST_FAILED")

        client.add_event_handler(on_message, events.NewMessage(chats=channel))
        client.add_event_handler(on_message, events.MessageEdited(chats=channel))
        await client.catch_up()
        await reconcile(client, channel, store, configured, wake)

        async def sender():
            while not stopped.is_set():
                wake.clear()
                if bot:
                    while not stopped.is_set():
                        results = await asyncio.gather(deliver_one(store, bot), deliver_one(store, bot))
                        if not any(results):
                            break
                        await asyncio.sleep(1.1)
                if stopped.is_set():
                    break
                try:
                    await asyncio.wait_for(wake.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass

        async def recovery():
            while True:
                await asyncio.sleep(60)
                if not client.is_connected():
                    log("RECONNECTING")
                    continue
                await reconcile(client, channel, store, configured, wake)
                log("HEARTBEAT", enabled=enabled, cursor=store.get("cursor"), counts=store.summary()["counts"])

        sender_task = asyncio.create_task(sender())
        tasks = [sender_task, asyncio.create_task(recovery())]
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stopped.set)
            except (NotImplementedError, RuntimeError):
                pass
        stop_task = asyncio.create_task(stopped.wait())
        disconnected = asyncio.ensure_future(client.disconnected)
        tasks += [stop_task, disconnected]
        log("WATCHING", source="jirum", enabled=enabled)
        done, _ = await asyncio.wait([*tasks, fatal], return_when=asyncio.FIRST_COMPLETED)
        if stop_task not in done:
            raise RuntimeError("WORKER_OR_CONNECTION_STOPPED")
    finally:
        # Let a running HTTP request finish before cancelling its asyncio task.
        # If systemd kills us, durable 'sending' becomes 'uncertain' next boot.
        stopped.set()
        wake.set()
        if sender_task and not sender_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(sender_task), timeout=30)
            except Exception:
                pass
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.disconnect()


async def check_or_verify(store, verify=False):
    configured = targets()
    bot = Bot()
    await asyncio.to_thread(bot.check_targets, configured)
    client, channel = await connected(configured)
    try:
        latest = await client.get_messages(channel, limit=1)
        if not latest:
            raise RuntimeError("SOURCE_EMPTY")
        post = extract(latest[0])
        log("PREFLIGHT_OK", source="jirum", post=post["id"], destinations=2, text_present=bool(post["text"]))
        if verify:
            parts = format_parts(post, test=True)
            if not parts:
                raise RuntimeError("LATEST_POST_HAS_NO_TEXT")
            verified = store.get("verification")
            if verified and verified["targets"] == [list(t) for t in configured]:
                previous = store.db.execute("SELECT body FROM posts WHERE scope=? AND id=?", (verified["scope"], post["id"])).fetchone()
                receipts = store.db.execute("SELECT text,status FROM delivery WHERE scope=?", (verified["scope"],)).fetchall()
                if previous and previous[0] == post["text"] and len(receipts) == 2 and all(r[0] == parts[0] and r[1] == "sent" for r in receipts):
                    log("TEST_ALREADY_DELIVERED_NO_RESEND", post=post["id"], destinations=2)
                    return
            scope = "test:" + uuid.uuid4().hex[:12]
            store.put(post["id"], post["text"], parts, configured, scope=scope)
            store.recover()
            while any(await asyncio.gather(deliver_one(store, bot, scope), deliver_one(store, bot, scope))):
                await asyncio.sleep(1.1)
            rows = store.db.execute("SELECT status FROM delivery WHERE scope=?", (scope,)).fetchall()
            if not rows or any(row[0] != "sent" for row in rows):
                raise RuntimeError("TEST_DELIVERY_INCOMPLETE_REVIEW_TEST_DATABASE")
            with store.db:
                store.set("verification", {"at": time.time(), "scope": scope, "targets": configured})
            log("TEST_DELIVERY_OK", scope=scope, post=post["id"], destinations=2)
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "check", "verify", "status", "resolve", "arm"))
    parser.add_argument("--env-file")
    parser.add_argument("--database", choices=("live", "shadow", "test"))
    parser.add_argument("--scope", default="live")
    parser.add_argument("--post", type=int)
    parser.add_argument("--destination", choices=("personal", "group"))
    parser.add_argument("--part", type=int, default=0)
    parser.add_argument("--outcome", choices=("sent", "retry"))
    parser.add_argument("--cutover-id", type=int)
    args = parser.parse_args()
    # Telethon/urllib third-party diagnostics may contain private metadata.
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    store = None
    try:
        load_env(args.env_file)
        enabled_value = os.environ.get("JIRUM_LIVE_ENABLED", "false")
        if enabled_value not in {"true", "false"}:
            raise RuntimeError("LIVE_FLAG_MUST_BE_TRUE_OR_FALSE")
        enabled = enabled_value == "true"
        state = Path(os.environ.get("JIRUM_DATA_DIR", "./live-data")).resolve()
        name = args.database or ("test" if args.command in {"check", "verify"} else "live" if enabled or args.command == "arm" else "shadow")
        if args.command == "run" and args.database:
            raise RuntimeError("RUN_DATABASE_SELECTED_BY_LIVE_FLAG_ONLY")
        lock = exclusive(state / "watcher.lock") if args.command != "status" else nullcontext()
        with lock:
            store = Store(state / ("jirum-" + name + ".sqlite3"))
            if args.command == "status":
                print(json.dumps(store.summary(), ensure_ascii=True))
            elif args.command == "resolve":
                if args.post is None or not args.destination or not args.outcome:
                    raise RuntimeError("RESOLUTION_ARGUMENTS_REQUIRED")
                store.resolve(args.scope, args.post, args.destination, args.part, args.outcome)
                log("OPERATOR_RESOLUTION_RECORDED")
            elif args.command == "arm":
                if name != "live" or not args.cutover_id or args.cutover_id < 1 or store.get("baseline") is not None:
                    raise RuntimeError("NEW_LIVE_DATABASE_AND_CUTOVER_ID_REQUIRED")
                receipt = Store(state / "jirum-test.sqlite3")
                try:
                    verified = receipt.get("verification")
                    configured = targets()
                    if not verified or time.time() - verified["at"] > 86400 or verified["targets"] != [list(t) for t in configured]:
                        raise RuntimeError("RECENT_TWO_DESTINATION_TEST_REQUIRED")
                    statuses = receipt.db.execute("SELECT status FROM delivery WHERE scope=?", (verified["scope"],)).fetchall()
                    if not statuses or any(r[0] != "sent" for r in statuses):
                        raise RuntimeError("RECENT_TWO_DESTINATION_TEST_REQUIRED")
                finally:
                    receipt.close()
                store.initialize(args.cutover_id, configured)
                log("CUTOVER_ARMED_NO_SEND", cursor=args.cutover_id)
            elif args.command in {"check", "verify"}:
                asyncio.run(check_or_verify(store, args.command == "verify"))
            else:
                asyncio.run(run_service(store, enabled))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Print only our fixed codes, never third-party exception details.
        known = str(exc) if type(exc) is RuntimeError and str(exc).replace("_", "").isupper() and str(exc).isascii() else "OPERATION_FAILED_DETAILS_HIDDEN"
        log("STOPPED", reason=known)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
