#!/usr/bin/env bash
# Runs ON the yaffo-peer VM (scp'd + executed by create.sh). Expects the
# source tarball at /tmp/yaffo_src.tar.gz and WEB_PORT in the environment.
# Idempotent: the venv and media dir survive re-runs; the source tree and
# the installed package are replaced every time (this is also the redeploy
# path after a local code change).
set -euo pipefail

WEB_PORT="${WEB_PORT:?WEB_PORT must be set}"

echo "==> apt packages"
sudo apt-get update -qq
# python3-dev + build-essential: insightface builds from sdist.
# libgl1 + libglib2.0-0: opencv-python's runtime libs. ffmpeg: video indexing.
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-dev build-essential ffmpeg libgl1 libglib2.0-0 curl >/dev/null

PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ "$PYV" != "3.13" ]]; then
  echo "system python3 is $PYV but yaffo pins ~=3.13.0 — use a debian-13 image" >&2
  exit 1
fi

echo "==> unpacking source"
sudo mkdir -p /opt/yaffo/data /opt/yaffo/media
sudo rm -rf /opt/yaffo/src
sudo mkdir -p /opt/yaffo/src
sudo tar xzf /tmp/yaffo_src.tar.gz -C /opt/yaffo/src
sudo chown -R "$USER" /opt/yaffo
rm -f /tmp/yaffo_src.tar.gz

echo "==> venv + pip install (first run takes a few minutes — insightface compiles)"
if [[ ! -d /opt/yaffo/venv ]]; then
  python3 -m venv /opt/yaffo/venv
  /opt/yaffo/venv/bin/pip -q install --upgrade pip
fi
# keyrings.alt: a headless VM has no OS keychain, and without any keyring
# backend the p2p device identity degrades to ephemeral-per-boot — pairing
# would break on every restart. The plaintext file backend keeps the
# identity stable; fine for a throwaway TEST instance, never for real use.
/opt/yaffo/venv/bin/pip -q install /opt/yaffo/src keyrings.alt
/opt/yaffo/venv/bin/python - <<'PY'
from pathlib import Path

from yaffo.utils.clip_tokenizer.tokenizer import _BPE_PATH

if not Path(_BPE_PATH).is_file():
    raise SystemExit(f"missing packaged CLIP tokenizer vocabulary: {_BPE_PATH}")
PY

echo "==> systemd unit"
sudo tee /etc/systemd/system/yaffo-peer.service >/dev/null <<UNIT
[Unit]
Description=Yaffo peer (temporary p2p test instance)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
Environment=YAFFO_DATA_DIR=/opt/yaffo/data
Environment=YAFFO_WEB_PORT=$WEB_PORT
Environment=PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring
ExecStart=/opt/yaffo/venv/bin/python -m yaffo
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable yaffo-peer.service >/dev/null 2>&1
sudo systemctl restart yaffo-peer.service

echo "==> waiting for the web server on 127.0.0.1:$WEB_PORT"
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$WEB_PORT/" >/dev/null 2>&1; then
    echo "  -> yaffo is serving"
    exit 0
  fi
  sleep 5
done
echo "yaffo did not start serving within 5 minutes; check: sudo journalctl -u yaffo-peer -e" >&2
exit 1
