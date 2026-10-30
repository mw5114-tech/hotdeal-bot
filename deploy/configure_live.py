"""Interactive secrets entry. Run on the server with sudo, or on Windows.

No secret values are printed. Existing files are never overwritten.
Optional --local-watcher reads only literal api_id/api_hash assignments, without
importing or executing watcher.py. It never reads the session database.
"""
import argparse
import ast
from getpass import getpass
import os
from pathlib import Path


def local_api(path):
    found = {}
    tree = ast.parse(Path(path).read_text("utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"api_id", "api_hash"}:
                found[name] = ast.literal_eval(node.value)
    if set(found) != {"api_id", "api_hash"}:
        raise ValueError("Local literal API ID/hash not found; use manual entry")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--local-watcher")
    parser.add_argument("--session-path", default="/var/lib/hotdeal-live/hotdeal_session.session")
    parser.add_argument("--data-dir", default="/var/lib/hotdeal-live")
    args = parser.parse_args()
    os.umask(0o077)
    found = local_api(args.local_watcher) if args.local_watcher else {}
    api_id = str(found.get("api_id") or input("Telegram API ID: ").strip())
    api_hash = str(found.get("api_hash") or getpass("Telegram API hash (hidden): ").strip())
    token = getpass("Existing bot token (hidden): ").strip()
    owner = getpass("Personal Telegram chat ID (hidden): ").strip()
    if not api_id.isdigit() or len(api_hash) != 32 or ":" not in token or not owner.isdigit():
        raise ValueError("Invalid credential format; values not saved")
    values = {"TELEGRAM_API_ID": api_id, "TELEGRAM_API_HASH": api_hash,
              "TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": owner,
              "TELEGRAM_GROUP_CHAT_ID": "", "TELETHON_SESSION_PATH": args.session_path,
              "JIRUM_DATA_DIR": args.data_dir, "JIRUM_LIVE_ENABLED": "false"}
    if any(any(c.isspace() for c in value) or "\x00" in value for value in values.values()):
        raise ValueError("Whitespace/control characters are not allowed")
    dest = Path(args.output).resolve()
    with dest.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(k + "=" + v + "\n" for k, v in values.items()))
    if os.name != "nt":
        dest.chmod(0o600)
    print("Configuration saved with automatic sending DISABLED. Values hidden.")
    if os.name == "nt":
        print("Use a private local folder and restrict its Windows ACL before SSH transfer.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Configuration not completed. Check paths and input format; no credential details shown.")
        raise SystemExit(1)
