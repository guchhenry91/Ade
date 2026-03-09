# Strategy Engine

Purpose: Strategic task prioritization across OpenClaw operations.

## Objectives
1. Maximize sports prediction accuracy
2. Increase TikTok account engagement
3. Find higher-quality job opportunities
4. Detect profitable betting signals

## Prioritization Rules
- Critical system health and watchdog issues always first.
- Time-sensitive sports lineup/news events outrank routine jobs.
- Approval-blocked tasks get elevated until resolved.
- Learning-engine insights adjust weights dynamically.

## Dynamic Priority Weights (example)
- system-health: 1.00
- sports-live-updates: 0.90
- approval-queue: 0.85
- betting-intelligence: 0.80
- tiktok-production: 0.70
- job-research: 0.65

## Commander Coordination
Commander reviews priority queue, reorders as needed, and logs decisions to:
- `workspace/agents/commander-status.md`
