# BOOT.md - OpenClaw System Bootstrap Brain

Purpose: This file is the system bootstrap script for OpenClaw. It instructs the agent to verify and rebuild the system environment on startup.

## Startup Bootstrap Rules

On every container restart or new session:

1. Verify workspace structure exists:
   - workspace/
   - workspace/agents/
   - workspace/jobs/
   - workspace/tiktok/
   - workspace/sports-predictions/
   - workspace/sports-betting/
   If any folder is missing, recreate it.

2. Verify agent architecture exists:
   - Commander Agent
   - Sports Prediction Agent
   - Betting Intelligence Agent
   - TikTok Content Agent
   - Job Research Agent
   If any agent definition is missing, rebuild it using the rules defined in `AGENTS.md`.

3. Verify scheduled jobs exist:
   - sports prediction worker
   - betting intelligence worker
   - tiktok content worker
   - job research worker
   - commander supervisor
   If cron jobs are missing, recreate them.

4. Verify Telegram routing exists:
   - workspace/agents/telegram-routes.json
   If routes are missing, recreate them using the main Telegram group.

5. Verify identity files exist:
   - SOUL.md
   - USER.md
   - IDENTITY.md
   - TOOLS.md
   - HEARTBEAT.md
   If any are missing, restore them from defaults.

6. Verify agent status directories exist:
   - workspace/agents/status/
   - workspace/agents/approvals/
   Create them if missing.

7. Log verification results to:
   - workspace/agents/commander-status.md

8. If any repair was performed, send a Telegram notification:
   - "System bootstrap repair executed."
