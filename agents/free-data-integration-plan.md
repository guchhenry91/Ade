# Free Data Integration Plan (NBA + Soccer)

## Goal
Improve prediction accuracy using only free sources, with repeatable ingestion and model refresh workflows.

## Source Stack

### NBA
- Fixtures/scores/team records: ESPN public endpoints
- Injuries/availability: NBA official injury report + team status pages
- Expected lineups: Rotowire free lineup page
- Odds baseline: manual bookmaker snapshot / OddsPortal checks

### Soccer (EPL/UCL/La Liga)
- Fixtures/scores/standings: ESPN public endpoints
- xG/xA/shooting/SOT: FBref + Understat
- Confirmed XI/injuries/suspensions: FotMob/Flashscore + official club pages
- Odds baseline: OddsPortal/manual books snapshot

## Daily Pipeline (UTC)

### 06:00 — Model Recalibration
1. Pull prior-day results
2. Grade prediction errors (win probs, player props)
3. Update calibration weights
4. Save to `workspace/agents/performance-memory/`

### Every 15 min — Pre-event scan
1. Detect upcoming matches/games (next 6h)
2. Refresh team/player context
3. Flag events entering T-3h window

### Every 10 min — News & lineup monitor
1. Check injuries/suspensions/status changes
2. Check confirmed lineups / inactives
3. Re-run affected predictions immediately

### T-3h to kickoff/tipoff — Publish window
1. Validate availability (hard gate)
2. Generate report sections:
   - Match/Game Analysis
   - Safest 3-Leg Parlay
   - Value/Upset Opportunities
3. Post to sports-analytics topic

### Post-game (within 30 min after completion)
1. Collect final outcomes
2. Score prediction hit/miss and calibration error
3. Append learning notes for next game

## Data Quality Rules
- Never publish player props without availability confirmation.
- Degrade confidence when lineup confirmation is missing.
- Use implied probability checks for value edges.
- Keep confidence bands conservative in high-draw soccer matchups.

## Implementation Checklist
- [ ] Add ingestion scripts:
  - `agents/workers/sources/fbref_ingest.py`
  - `agents/workers/sources/understat_ingest.py`
  - `agents/workers/sources/fotmob_lineups_ingest.py`
  - `agents/workers/sources/odds_snapshot_ingest.py`
- [ ] Add normalized storage:
  - `workspace/agents/data/raw/`
  - `workspace/agents/data/normalized/`
- [ ] Add post-game grader:
  - `agents/workers/postgame_grader.py`
- [ ] Add calibration artifact:
  - `workspace/agents/performance-memory/calibration.json`

## Success Metrics
- Better probability calibration (Brier score trend down)
- Higher edge precision vs market implied probability
- Fewer invalid player picks due to late availability changes
- Improved consistency of safest ticket hit-rate
