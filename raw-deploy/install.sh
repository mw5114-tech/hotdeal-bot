#!/usr/bin/env bash
# Run on an existing Ubuntu VM: sudo bash deploy/install.sh
# Installs application/service but NEVER enables or starts it.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
  echo 'Run with sudo on the Ubuntu server.' >&2
  exit 1
fi
src="$(cd -- "$(dirname -- "$0")/.." && pwd)"
if ! command -v apt-get >/dev/null; then
  echo 'Ubuntu apt-get is required.' >&2
  exit 1
fi
apt-get update
apt-get install -y python3 python3-venv ca-certificates
if ! id hotdeal-live >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/hotdeal-live --shell /usr/sbin/nologin hotdeal-live
fi
install -d -o root -g root -m 0755 /opt/hotdeal-live
install -d -o hotdeal-live -g hotdeal-live -m 0700 /var/lib/hotdeal-live
for file in watcher.py raw_runtime.py jirum_github_dispatch.py raw_message.py raw_delivery.py live_store.py telegram_delivery.py requirements-live.txt; do
  install -o root -g root -m 0644 "$src/$file" "/opt/hotdeal-live/$file"
done
install -d -o root -g root -m 0755 /opt/hotdeal-live/state
install -o root -g root -m 0644 "$src/state/telegram_group_chat.json" /opt/hotdeal-live/state/telegram_group_chat.json
install -d -o root -g root -m 0755 /opt/hotdeal-live/deploy
for file in backup_state.py run.sh; do
  install -o root -g root -m 0644 "$src/raw-deploy/$file" "/opt/hotdeal-live/deploy/$file"
done
python3 -m venv /opt/hotdeal-live/.venv
/opt/hotdeal-live/.venv/bin/python -m pip install --disable-pip-version-check -r /opt/hotdeal-live/requirements-live.txt
install -o root -g root -m 0644 "$src/raw-deploy/hotdeal-live.service" /etc/systemd/system/hotdeal-live.service
systemctl daemon-reload
echo 'Installed; not started. Configure secrets/session, then run preflight and shadow tests.'
