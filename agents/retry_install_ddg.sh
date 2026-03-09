#!/usr/bin/env bash
set -euo pipefail
WORKDIR="/data/.openclaw/workspace"
LOG="$WORKDIR/agents/status/ddg-install-retry.log"
mkdir -p "$WORKDIR/agents/status"

for i in {1..6}; do
  ts=$(date -u +"%F %T UTC")
  echo "[$ts] attempt $i: installing ddg-web-search" >> "$LOG"

  if clawhub install ddg-web-search --workdir "$WORKDIR" >> "$LOG" 2>&1; then
    echo "[$(date -u +"%F %T UTC")] install success" >> "$LOG"
    python3 - <<'PY'
import json, urllib.parse, urllib.request, pathlib, os
root=pathlib.Path('/data/.openclaw/workspace')
cfgp=root/'agents'/'reporting-config.json'
routesp=root/'agents'/'telegram-routes.json'
if cfgp.exists() and routesp.exists():
    cfg=json.loads(cfgp.read_text())
    token=os.getenv(cfg.get('reporting_bot',{}).get('env_token_key','TELEGRAM_REPORTING_BOT_TOKEN')) or cfg.get('reporting_bot',{}).get('token') or os.getenv('TELEGRAM_BOT_TOKEN')
else:
    token=os.getenv('TELEGRAM_BOT_TOKEN')
if token:
    q=urllib.parse.urlencode({'chat_id':'8385872564','text':'ddg-web-search is installed successfully ✅'})
    urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage?{q}',timeout=20).read()
PY
    exit 0
  fi

  sleep 1200
 done

python3 - <<'PY'
import os, urllib.parse, urllib.request
token=os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_REPORTING_BOT_TOKEN')
if token:
    q=urllib.parse.urlencode({'chat_id':'8385872564','text':'ddg-web-search install still rate-limited after retries. I will retry on request.'})
    urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage?{q}',timeout=20).read()
PY
