# Learning Engine

Purpose: Improve agent behavior using historical outcomes.

## Sports Prediction Agent
- Compare predicted outcomes vs actual match/game results
- Track win-rate and calibration quality
- Adjust confidence weighting by league/model context

## TikTok Content Agent
- Track views, likes, shares, completion rate
- Identify viral hooks, sounds, formats
- Improve hook and concept generation

## Job Research Agent
- Track interview/application success rate
- Detect stronger sources and role-fit signals
- De-prioritize low-quality or underpaid listings

## Data Use Rule
Agents must consult `workspace/mission-control/learning-data.json` before execution.

## Storage
- Insights store: `workspace/mission-control/learning-data.json`
- Goal metrics: `workspace/mission-control/goals.json`
- Operations and approval trace: `workspace/mission-control/operations-log.md`
