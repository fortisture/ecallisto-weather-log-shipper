#!/usr/bin/env bash
# Installs the eCallisto weather log sender as a systemd service on the Pi.
#
# Usage: ./setup_pi.sh <watch_dir> <config_json> [install_dir]
#   watch_dir    directory on this Pi containing the *.csv weather logs
#   config_json  a filled-in copy of pi/config.example.json (host/port/token/fingerprint)
#   install_dir  defaults to /opt/ecallisto-weather-shipper
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <watch_dir> <config_json> [install_dir]" >&2
  exit 1
fi

WATCH_DIR="$1"
CONFIG_SRC="$2"
INSTALL_DIR="${3:-/opt/ecallisto-weather-shipper}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$WATCH_DIR" ]; then
  echo "error: watch dir '$WATCH_DIR' does not exist" >&2
  exit 1
fi
if [ ! -f "$CONFIG_SRC" ]; then
  echo "error: config file '$CONFIG_SRC' not found" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found on PATH" >&2
  exit 1
fi

RUN_USER="$(whoami)"
STATE_DIR="/var/lib/ecallisto-weather-shipper"

sudo mkdir -p "$INSTALL_DIR" "$STATE_DIR"
# The service runs as $RUN_USER (not root), so it needs write access to the
# state directory and read access to config.json -- both are created via
# sudo below and would otherwise end up root-owned and inaccessible to it.
sudo chown "$RUN_USER" "$STATE_DIR"
sudo cp "$SCRIPT_DIR/sender.py" "$INSTALL_DIR/sender.py"

sudo python3 - "$CONFIG_SRC" "$WATCH_DIR" "$INSTALL_DIR/config.json" <<PYEOF
import json, sys
src, watch_dir, dst = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src) as f:
    cfg = json.load(f)
for key in ("host", "port", "token", "fingerprint"):
    if key not in cfg or str(cfg[key]).startswith("REPLACE_WITH"):
        sys.exit(f"error: config field '{key}' is missing or still a placeholder")
cfg["watch_dir"] = watch_dir
cfg.setdefault("state_file", "$STATE_DIR/state.json")
with open(dst, "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF

sudo chown "$RUN_USER" "$INSTALL_DIR/config.json"
sudo chmod 600 "$INSTALL_DIR/config.json"

sudo tee /etc/systemd/system/weather-shipper.service > /dev/null <<UNIT
[Unit]
Description=eCallisto weather log shipper
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $INSTALL_DIR/sender.py $INSTALL_DIR/config.json
Restart=always
RestartSec=5
User=$RUN_USER

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now weather-shipper.service

echo ""
echo "Installed and started. Useful commands:"
echo "  sudo systemctl status weather-shipper"
echo "  journalctl -u weather-shipper -f"
