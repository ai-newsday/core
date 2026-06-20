#!/usr/bin/env bash
# 注册 Telegram webhook 指向已部署的 Worker。
# 需要环境变量: TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, WORKER_URL(例 https://xxx.workers.dev)
set -euo pipefail
: "${TELEGRAM_BOT_TOKEN:?need TELEGRAM_BOT_TOKEN}"
: "${WEBHOOK_SECRET:?need WEBHOOK_SECRET}"
: "${WORKER_URL:?need WORKER_URL}"

curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WORKER_URL%/}/tg" \
  --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
  --data-urlencode 'allowed_updates=["callback_query"]'
echo
echo "已注册。验证: curl https://api.telegram.org/bot\$TELEGRAM_BOT_TOKEN/getWebhookInfo"
