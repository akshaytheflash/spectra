#!/usr/bin/env bash
# SPECTRA backend bootstrap for Ubuntu (Oracle Cloud / any VPS).
# Run as the default user with sudo rights, from the repo root:
#   bash deploy/vm_setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "==> project root: $ROOT"
ARCH="$(uname -m)"
echo "==> arch: $ARCH  cpus: $(nproc)  mem: $(free -m | awk '/Mem:/{print $2}')MB"

echo "==> apt packages"
sudo apt-get update -y -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-dev build-essential pkg-config \
  ffmpeg tesseract-ocr tesseract-ocr-hin \
  libzbar0 libgl1 libglib2.0-0 curl ca-certificates

echo "==> python venv"
[ -d "$ROOT/.venv" ] || python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -q -U pip wheel setuptools

# torch: CPU build only, never the 2.5 GB CUDA wheel.
if ! "$ROOT/.venv/bin/python" -c "import torch" 2>/dev/null; then
  echo "==> torch (cpu)"
  if [ "$ARCH" = "x86_64" ]; then
    "$ROOT/.venv/bin/pip" install -q --index-url https://download.pytorch.org/whl/cpu torch
  else
    "$ROOT/.venv/bin/pip" install -q torch
  fi
fi

echo "==> requirements"
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"

echo "==> systemd unit"
sudo tee /etc/systemd/system/spectra.service >/dev/null <<UNIT
[Unit]
Description=SPECTRA OSINT API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$ROOT
Environment=PYTHONPATH=$ROOT/backend
Environment=PYTHONUTF8=1
Environment=HF_HOME=$ROOT/.models/hf
Environment=OMP_NUM_THREADS=2
Environment=SPECTRA_TORCH_THREADS=1
EnvironmentFile=-$ROOT/.env
ExecStart=$ROOT/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=3
StandardOutput=append:/var/log/spectra.log
StandardError=append:/var/log/spectra.log

[Install]
WantedBy=multi-user.target
UNIT

sudo touch /var/log/spectra.log
sudo chown "$USER" /var/log/spectra.log
sudo systemctl daemon-reload
sudo systemctl enable spectra >/dev/null 2>&1 || true
sudo systemctl restart spectra

echo "==> firewall (porthole for 8000)"
# Oracle Ubuntu images ship a REJECT-all INPUT chain; insert before it.
sudo iptables -C INPUT -p tcp --dport 8000 -j ACCEPT 2>/dev/null || \
  sudo iptables -I INPUT 6 -p tcp --dport 8000 -j ACCEPT
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent
sudo netfilter-persistent save >/dev/null 2>&1 || true

echo "==> waiting for API"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS http://127.0.0.1:8000/health && echo
echo "==> done"
