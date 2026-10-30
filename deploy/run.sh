#!/usr/bin/env bash
# Run a one-off command with root-owned secrets, as the unprivileged user.
# Does not print the environment or put credentials on a command line.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
  echo 'Run with sudo.' >&2
  exit 1
fi
case "${1:-}" in
  check|verify|status|resolve|arm) ;;
  *) echo 'Usage: sudo bash deploy/run.sh check|verify|status|resolve|arm [arguments]' >&2; exit 1 ;;
esac
exec systemd-run --quiet --wait --pipe --collect \
  --property=User=hotdeal-live --property=Group=hotdeal-live \
  --property=EnvironmentFile=/etc/hotdeal-live.env \
  --property=WorkingDirectory=/opt/hotdeal-live \
  --property=UMask=0077 \
  /opt/hotdeal-live/.venv/bin/python /opt/hotdeal-live/jirum_live.py "$@"
