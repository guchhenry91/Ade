# Operator Notification Policy

Operator:
- Name: Ade
- Telegram username: @HenryAgentsbot
- Telegram user ID: 8385872564

Rule: every important agent message must begin with `@HenryAgentsbot`.
Additional ping target for TikTok production updates: `@Jasonopen`.

## Trigger events
Agents notify when:
- task completed
- approval required
- error occurs
- critical system event occurs
- scheduled job finishes

## Routing topics
- sports-analytics -> Sports Prediction Agent
- tiktok-production -> TikTok Content Agent
- job-opportunities -> Job Research Agent
- betting-insights -> Betting Intelligence Agent
- commander-control -> Commander Agent

## Message formats
- Completion: `@HenryAgentsbot Task completed by <agent name>`
- Approval: `@HenryAgentsbot Approval required: <task/action>`
- Error: `@HenryAgentsbot Alert: <agent name> encountered an error` (+short reason)

## Commander escalation
Commander notifies operator for:
- repeated agent failures
- system health warnings
- watchdog repairs
- system configuration changes
- unusual betting signals / major prediction shifts

## Daily briefing
- Schedule: 09:00 UTC daily
- Topic: commander-control
- Title: Mission Control Daily Briefing
- Must summarize Sports, TikTok, Jobs, and System Health

All notifications and briefings are logged in:
- `workspace/agents/commander-status.md`
