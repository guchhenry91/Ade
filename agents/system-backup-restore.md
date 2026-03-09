# OpenClaw Backup & Restore

## Backup engine
- Script: `workspace/agents/backup_openclaw.sh`
- Schedule: daily at 02:00 (see `workspace/jobs/topic-routing-cron.txt`)
- Targets:
  - `/data/.openclaw/`
  - `/data/.openclaw/workspace/`
  - `/data/.openclaw/agents/`
  - `/data/.openclaw/canvas/`
- Destination: `/data/backups/openclaw/`
- Retention: last 14 archives
- Verification: `gzip -t` integrity check
- Logs: `workspace/agents/system-backups.log`

## Restore system
- Command format:
  - `restore_openclaw --latest`
  - `restore_openclaw --snapshot YYYY-MM-DD`
- Script path in this environment:
  - `/data/.openclaw/workspace/agents/restore_openclaw`
- If `restore_openclaw` is not in PATH, execute with full path.

Restore flow:
1. Verify backup integrity
2. Stop container/services (best effort)
3. Restore archive
4. Verify workspace + agent config files
5. Restart OpenClaw services (best effort)
6. Verify Telegram connectivity
7. Notify commander-control on start/complete/failure

Logs: `workspace/agents/system-restore.log`
