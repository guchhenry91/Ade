# Commander Watchdog

Location: `workspace/agents/commander-watchdog.md`

## Purpose
Continuously monitor and auto-repair the OpenClaw platform so the VPS multi-agent system can self-recover without operator intervention.

## Scope
The Commander Watchdog monitors and repairs:
1. Container runtime
2. Agent health
3. Cron scheduler
4. Telegram routing
5. Workspace integrity
6. System resources
7. Log failure patterns

## Monitored Components and Repair Actions

### 1) Container runtime
Checks:
- OpenClaw container running
- gateway responding
- websocket server active

If failure detected:
- attempt automatic recovery
- restart affected services
- send alert to commander-control

### 2) Agent health
Agents:
- Commander Agent
- Sports Prediction Agent
- Betting Intelligence Agent
- TikTok Content Agent
- Job Research Agent

For each agent verify:
- last execution timestamp
- worker status
- error logs

If agent misses scheduled execution window:
- restart agent worker
- notify commander-control

### 3) Cron scheduler
Verify scheduled jobs exist for:
- sports predictions
- betting intelligence monitoring
- TikTok production
- job research
- commander supervision

If missing:
- recreate cron entries automatically
- log repair

### 4) Telegram routing
Verify:
- `workspace/agents/telegram-routes.json` exists
- each topic has `chat_id` + `thread_id`

If missing/corrupted:
- rebuild routing config
- reinitialize topic listener
- notify commander-control

### 5) Workspace integrity
Required directories:
- `workspace/agents`
- `workspace/agents/status`
- `workspace/agents/approvals`
- `workspace/sports-predictions`
- `workspace/sports-betting`
- `workspace/tiktok`
- `workspace/jobs`

If missing:
- recreate directory
- log repair

### 6) System resource monitoring (every 10 minutes)
Check thresholds:
- CPU > 90%
- memory > 90%
- disk > 85%

If exceeded:
- send warning alert to commander-control

### 7) Log monitoring
Scan recent logs for:
- repeated agent failures
- API errors
- Telegram connection failures

If repeated failures detected:
- escalate alert to commander-control

## Schedule
- Run Watchdog every 5 minutes.

## Logging
- Log each run to: `workspace/agents/commander-status.md`

## Telegram Alert Policy
Send alerts only for:
- agent failure
- cron repair
- routing repair
- resource warning
- container restart

Message format:
`[Watchdog] Issue detected → repair attempted.`

## Runtime implementation
- Watchdog runner: `workspace/agents/commander_watchdog.py`
- Cron profile entry: `*/5 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/commander_watchdog.py`
