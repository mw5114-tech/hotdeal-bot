"""Create a consistent SQLite backup; includes outbox text, no session keys.
Run as the service user; store the output in a private directory off the VM.
"""
import argparse
from pathlib import Path
import sqlite3
import os

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source")
parser.add_argument("destination")
args = parser.parse_args()
os.umask(0o077)
source = Path(args.source).resolve()
destination = Path(args.destination).resolve()
if not source.is_file() or destination.exists() or source == destination:
    raise SystemExit("Source required; destination must be new")
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
    with sqlite3.connect(destination) as dest:
        src.backup(dest)
print("Backup complete; keep private and copy off the server via SSH.")
