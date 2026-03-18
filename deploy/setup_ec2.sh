#!/bin/sh
# setup_ec2.sh — One-shot EC2 bootstrap for predict-market-bot
# Run once after cloning the repo on morningside-vps.
#
# Usage:
#   chmod +x deploy/setup_ec2.sh && ./deploy/setup_ec2.sh

set -e

REPO_DIR="/home/ssm-user/.openclaw/workspaces/predict-market-bot"

echo "[setup] Moving to repo directory: $REPO_DIR"
cd "$REPO_DIR"

echo "[setup] Creating Python 3.12 virtual environment"
python3.12 -m venv .venv
. .venv/bin/activate

echo "[setup] Installing dependencies"
pip install --upgrade pip
pip install -r requirements.txt

echo "[setup] Creating required directories"
mkdir -p logs data docs/incidents

echo "[setup] Setting up .env from template"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[setup] .env created — fill in credentials manually before starting services"
else
    echo "[setup] .env already exists — skipping"
fi

echo "[setup] Installing systemd units"
sudo cp deploy/predict-market-pipeline.service  /etc/systemd/system/
sudo cp deploy/predict-market-pipeline.timer    /etc/systemd/system/
sudo cp deploy/predict-market-nightly.service   /etc/systemd/system/
sudo cp deploy/predict-market-nightly.timer     /etc/systemd/system/
sudo cp deploy/predict-market-metrics.service   /etc/systemd/system/

echo "[setup] Reloading systemd"
sudo systemctl daemon-reload

echo "[setup] Enabling and starting pipeline timer"
sudo systemctl enable --now predict-market-pipeline.timer

echo "[setup] Enabling and starting nightly timer"
sudo systemctl enable --now predict-market-nightly.timer

echo "[setup] Enabling and starting metrics server"
sudo systemctl enable --now predict-market-metrics.service

echo ""
echo "[setup] Done. Check service status with:"
echo "  sudo systemctl status predict-market-pipeline.timer"
echo "  sudo systemctl status predict-market-metrics.service"
echo ""
echo "IMPORTANT: Edit .env with real API credentials before trading live."
