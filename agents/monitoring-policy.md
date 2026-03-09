# Telegram Topic Monitoring Architecture

- One Telegram supergroup: `-1003659714036`
- Topics:
  - sports-analytics → predictions and match analysis
  - betting-insights → value bets and odds monitoring
  - tiktok-production → scripts, approvals, finished videos
  - job-opportunities → job research results
  - commander-control → system alerts and approvals

## Agent Posting Rule
Each agent posts only in its assigned topic, using `agents/telegram-routes.json` + `agents/agent-topic-bindings.json`.

## Global Autonomy Rule
All operational agents must execute normal in-scope tasks immediately without asking for permission.

Agents must not ask prompts like:
- "Do you want me to run this?"
- "Should I generate this?"
- "Would you like additional analysis?"

If task is within responsibility, agent must:
- perform task
- generate full output
- deliver results directly

Applies to:
- Sports Prediction Agent
- Betting Intelligence Agent
- TikTok Content Agent
- Job Research Agent
- any temporary/specialist agents created by Agent Architect

## Security Exception Rule (approval required)
The following must go through Commander -> approval workflow -> user confirmation:
- modifying system configuration files
- container/server control commands
- changing OpenClaw core settings
- accessing credentials or tokens
- auto-apply job submissions
- financial transactions or betting execution
- deleting files or altering system state

## Commander Oversight
Commander must:
- enforce global autonomy for normal tasks
- monitor all topic outputs
- verify agent actions
- flag anomalies
- approve sensitive actions only under security exception rule
- log decisions to `workspace/agents/commander-status.md`

## Advanced skill enforcement
Commander enforces global skill framework for all agents:
- strategic_planning
- continuous_monitoring
- trend_intelligence
- performance_memory
- dynamic_agent_creation
- risk_analysis
- smart_scheduler
- system_self_healing
- mission_control_insight
- decision_escalation

Decision escalation is mandatory when confidence <60%, conflicting signals appear, or security/financial actions are involved.

## Continuous topic integration
`agents/telegram_topic_listener.py` runs on a lightweight schedule to detect new topics and update routing.
