#!/usr/bin/env bash
# Первичный сетап бота на чистом VPS (Ubuntu/Debian). Ставит python3/venv/git
# (нужны root-права, напрямую или через sudo), сервис при этом настраивается
# на запуск от того юзера, который вызвал этот скрипт.
set -euo pipefail

REPO_URL="${1:?Usage: setup_vps.sh <git-repo-url>}"
APP_DIR="/opt/telegram-vk-bot"
RUN_USER="${SUDO_USER:-$(id -un)}"

apt-get update -y
apt-get install -y python3 python3-venv git

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo ">> Заполни $APP_DIR/.env реальными значениями перед стартом сервиса!"
fi

chown -R "$RUN_USER" "$APP_DIR"

sed "s/^User=.*/User=$RUN_USER/" "$APP_DIR/deploy/telegram-vk-bot.service" > /etc/systemd/system/telegram-vk-bot.service
systemctl daemon-reload
systemctl enable telegram-vk-bot

echo ">> Сервис настроен на запуск от юзера: $RUN_USER"
echo ">> Готово. После заполнения .env выполни: systemctl start telegram-vk-bot"
