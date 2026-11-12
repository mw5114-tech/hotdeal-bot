#!/usr/bin/env python3
"""Read-only public-repository credential audit.

The scanner never prints matched values and never reads GitHub Actions Secrets.
It has no dependency on the publishing pipeline and does not change state.
This is a high-confidence guard, not a complete historical secret scan.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

MAX_TEXT_BYTES = 2_000_000
MAX_REPORTS = 30

# These files are unsafe to track even when their contents do not match a token regex.
FORBIDDEN_FILENAMES = {
    "storage_state.json", "storage-state.json", "cookies.json", "auth.json",
    "credentials.json", "id_rsa", "id_ed25519",
}
FORBIDDEN_SUFFIXES = (".pem", ".p12", ".pfx", ".har", ".trace.zip",
                      ".secrets.json", ".session", ".session-journal")
ALLOW_DOTENV = {".env.example", ".env.sample", ".env.template"}
TOKEN_PATTERNS = {
    "GitHub access token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "Telegram bot token": re.compile(r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Google API key": re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "Private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "Stripe secret key": re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
}
CONFIG_ASSIGNMENT = re.compile(
    r"""(?im)^\s*(?:export\s+)?(?:TELEGRAM_BOT_TOKEN|NAVER_PASSWORD|NAVER_BLOG_STORAGE_STATE_JSON|COUPANG_PARTNERS_ACCESS_KEY|COUPANG_PARTNERS_SECRET_KEY|GITHUB_ACTIONS_TOKEN|JIRUM_GITHUB_DISPATCH_TOKEN|KAKAO_DEV_STORAGE_STATE_JSON)\s*(?::|=)\s*['"]?([A-Za-z0-9+/_.=-]{18,})"""
)

def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], check=True,
                            stdout=subprocess.PIPE)
    return [Path(p.decode("utf-8", "surrogateescape"))
            for p in result.stdout.split(b"\0") if p]

def forbidden_path(path: Path) -> bool:
    parts = path.parts
    name = path.name.lower()
    if name.startswith(".env") and name not in ALLOW_DOTENV:
        return True
    if name in FORBIDDEN_FILENAMES or name.endswith(FORBIDDEN_SUFFIXES):
        return True
    return ".auth" in parts or ".coupang_partner_profile" in parts

def safe_label(path: Path) -> str:
    # Avoid terminal control codes from unexpected file names in public logs.
    return re.sub(r"[^A-Za-z0-9_./-]", "_", str(path))[:180]

def main() -> int:
    findings: list[tuple[str, str, int]] = []
    paths = tracked_files()
    for path in paths:
        label = safe_label(path)
        if forbidden_path(path):
            findings.append((label, "Sensitive file path tracked", 0))
            continue
        if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        data = raw.decode("utf-8", errors="replace")
        for kind, pattern in TOKEN_PATTERNS.items():
            for match in pattern.finditer(data):
                line = data.count("\n", 0, match.start()) + 1
                findings.append((label, kind, line))
                if len(findings) >= MAX_REPORTS:
                    break
            if len(findings) >= MAX_REPORTS:
                break
        if len(findings) >= MAX_REPORTS:
            break
        for match in CONFIG_ASSIGNMENT.finditer(data):
            value = match.group(1)
            # Ignore references to placeholders, sample text or environment readers.
            if value.lower().startswith(("example", "placeholder", "os.environ",
                                          "os.getenv", "secrets.", "github.")):
                continue
            findings.append((label, "Possible inline credential assignment",
                             data.count("\n", 0, match.start()) + 1))
            if len(findings) >= MAX_REPORTS:
                break
        if len(findings) >= MAX_REPORTS:
            break
    if findings:
        for name, kind, line in findings:
            where = f"{name}:{line}" if line else name
            print(f"SECURITY_FINDING {where}: {kind} (value hidden)")
        print("SECURITY_AUDIT_FAILED: review files; rotate credentials if exposed.")
        return 1
    print(f"SECURITY_AUDIT_OK: checked {len(paths)} tracked paths; no high-confidence plaintext secrets found.")
    print("Note: prior commits, GitHub logs, and unknown token formats are outside this audit.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
