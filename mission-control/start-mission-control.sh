#!/usr/bin/env bash
set -e
cd /data/.openclaw/workspace
if ! pgrep -f "mission-control/server.py" >/dev/null 2>&1; then
  nohup /usr/bin/python3 /data/.openclaw/workspace/mission-control/server.py >/data/.openclaw/workspace/agents/status/mission-control.log 2>&1 &
fi
