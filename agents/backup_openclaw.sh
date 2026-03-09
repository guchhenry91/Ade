#!/usr/bin/env bash
set -euo pipefail

BACKUP_ROOT="/data/backups/openclaw"
TS="$(date +%F_%H%M%S)"
ARCHIVE="$BACKUP_ROOT/openclaw-$TS.tar.gz"
LOG="/data/.openclaw/workspace/agents/system-backups.log"
REPORT_CFG="/data/.openclaw/workspace/agents/reporting-config.json"
ROUTES="/data/.openclaw/workspace/agents/telegram-routes.json"

mkdir -p "$BACKUP_ROOT"
mkdir -p "$(dirname "$LOG")"

echo "[$(date -u +'%F %T UTC')] backup start: $ARCHIVE" >> "$LOG"

if tar -czf "$ARCHIVE" /data/.openclaw /data/.openclaw/workspace /data/.openclaw/agents /data/.openclaw/canvas 2>>"$LOG"; then
  if gzip -t "$ARCHIVE" 2>>"$LOG"; then
    echo "[$(date -u +'%F %T UTC')] backup success: $ARCHIVE" >> "$LOG"
  else
    echo "[$(date -u +'%F %T UTC')] backup integrity check failed: $ARCHIVE" >> "$LOG"
    python3 /data/.openclaw/workspace/agents/notify_commander.py "@HenryAgentsbot Backup failed: archive integrity check failed." || true
    exit 1
  fi
else
  echo "[$(date -u +'%F %T UTC')] backup failed: archive create error" >> "$LOG"
  python3 /data/.openclaw/workspace/agents/notify_commander.py "@HenryAgentsbot Backup failed: archive creation error." || true
  exit 1
fi

ls -1t "$BACKUP_ROOT"/openclaw-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f

DISK_PCT=$(df /data | awk 'NR==2 {gsub("%","",$5); print $5}')
if [ "${DISK_PCT:-0}" -ge 85 ]; then
  echo "[$(date -u +'%F %T UTC')] warning: disk low ${DISK_PCT}%" >> "$LOG"
  python3 /data/.openclaw/workspace/agents/notify_commander.py "@HenryAgentsbot Backup warning: low disk space (${DISK_PCT}%)." || true
fi
