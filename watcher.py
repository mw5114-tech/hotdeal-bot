"""@jirum original text -> existing Telegram bot -> personal + MUSE group."""
from pathlib import Path
import sys
from raw_runtime import main

if __name__ == "__main__":
    # Preserve the familiar `python watcher.py` launch command.
    if len(sys.argv) == 1:
        sys.argv.append("run")
    local_env = Path(__file__).resolve().parent / ".env"
    if "--env-file" not in sys.argv and local_env.is_file():
        sys.argv += ["--env-file", str(local_env)]
    raise SystemExit(main())
