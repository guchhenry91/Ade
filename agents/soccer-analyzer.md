# Soccer Analyzer Specialist Capability

Name: `soccer_analyzer`
Orchestrator: Commander Agent (dynamic spawn via Agent Architect)

## Supported competitions
- English Premier League
- UEFA Champions League
- La Liga

## Analysis metrics
- expected goals (xG)
- expected goals conceded
- team form (last 5 matches)
- home vs away performance
- head-to-head statistics
- injuries and suspensions
- possession metrics
- shot creation metrics
- defensive structure
- expected possession value (EPV)

## Output per match
1. Predicted winner
2. Projected scoreline
3. Win probability percentage
4. Confidence score
5. Key tactical factors

## Autonomous behavior
Using `continuous_monitoring`, Soccer Analyzer tracks:
- injury news
- lineup announcements
- tactical changes
- coaching decisions
- late team news

When significant changes occur:
- recalculate predictions
- update probability models
- notify Commander

## Integration flow
Commander -> spawn Soccer Analyzer -> tactical/statistical analysis
Sports Prediction Agent -> probability projections
Betting Intelligence Agent -> value detection
Commander -> publish final summary in sports-analytics topic

## Storage + reporting
- Detailed report: `workspace/sports-analysis/YYYY-MM-DD.md`
- Telegram summary topic: `sports-analytics`
