# Sports Betting Model

Multi-sport betting model covering **NBA · NFL · Premier League · Champions League · La Liga · Ligue 1**.

## Markets Covered

| Sport | Market |
|-------|--------|
| Soccer | Match Winner (1X2) |
| Soccer | Over/Under Total Goals |
| Soccer | Both Teams to Score (BTTS) |
| Soccer | Expected Goals (xG) comparison vs market line |
| Soccer | Player Shot Attempts O/U |
| Soccer | Player Anytime Scorer probability |
| NBA | Game Winner (moneyline) |
| NBA | Total Points O/U |
| NBA | Player Points / Rebounds / Assists / 3PM O/U |
| NBA | Season Average O/U (season-to-date vs projection) |
| NFL | Game Winner (moneyline) |
| NFL | Against the Spread |
| NFL | Total Points O/U |
| NFL | Anytime TD Scorer |
| NFL | Rushing / Receiving / Passing Yards O/U |

## Data Sources

| Source | Used For | Key Required |
|--------|----------|--------------|
| ESPN public API | Fixtures, scoreboard, team stats | No |
| api-football.com | Soccer player stats, xG data | Yes (free tier: 100 req/day) |
| balldontlie.io | NBA player season averages, game logs | Optional (free tier) |

## Setup

```bash
cd sports-betting-model
pip install -r requirements.txt

# Optional – higher data quality
export API_FOOTBALL_KEY=your_key_here
export BALLDONTLIE_KEY=your_key_here
```

Get free keys:
- **api-football.com**: https://dashboard.api-football.com (100 req/day free)
- **balldontlie.io**: https://www.balldontlie.io (free tier available)

## Usage

### Run all leagues today
```bash
python main.py
```

### Soccer only
```bash
python main.py --sports soccer
python main.py --sports soccer --league EPL
python main.py --sports soccer --league UCL
python main.py --sports soccer --league LIGA   # La Liga
python main.py --sports soccer --league L1     # Ligue 1
```

### Analyse a custom fixture
```bash
python main.py --fixture "Manchester City" "Arsenal" --league EPL
python main.py --fixture "Real Madrid" "Barcelona" --league LIGA
python main.py --fixture "PSG" "Marseille" --league L1
```

### NBA
```bash
# Today's games
python main.py --sports nba

# Player prop
python main.py --player-prop "LeBron James" --lines pts=25.5,reb=7.5,ast=8.5

# Season average O/U
python main.py --season-avg
```

### NFL
```bash
# Today / current week
python main.py --sports nfl

# Anytime TD scorer for a team (use ESPN team ID)
python main.py --td-scorer --team-id 1
```

### Value filter
```bash
# Only show bets with ≥3% edge vs market odds
python main.py --value-only --min-edge 3.0
```

### Save report
```bash
python main.py --save-report
# → reports/2026-03-22.md
```

## Model Architecture

```
main.py                   CLI entry point
├── models/
│   ├── football_model.py  Soccer: xG Poisson model (Dixon-Coles inspired)
│   ├── nba_model.py       NBA: net-rating logistic model + player props
│   └── nfl_model.py       NFL: spread-based win prob + TD Poisson model
├── data/
│   ├── fetcher.py         HTTP client with cache (TTL=1h) + retry
│   ├── football_data.py   ESPN + api-football soccer data
│   ├── nba_data.py        ESPN + balldontlie NBA data
│   └── nfl_data.py        ESPN NFL data
├── markets/
│   ├── value_engine.py    Edge filter, Kelly sizing, signal ranking
│   └── report.py          Rich console tables + markdown export
└── utils/
    ├── stats.py           Poisson, Normal, xG, TD probability functions
    └── odds.py            BetSignal dataclass, odds conversions
```

## Statistical Methods

### Soccer xG Model
- **Dixon-Coles** attack/defence strength estimation per team
- **Independent Poisson** distributions for home/away goals
- Match winner (1X2), BTTS, and O/U probabilities derived analytically
- Player scorer probability: `P(score≥1) = 1 − (1 − xG/shot)^shots`

### NBA Win Probability
- **Net-rating logistic model**: `P(win) = σ(net_rating_diff + HCA)`
- Home court advantage: +3.5 pts calibrated to historical data
- **Total points**: pace × offensive rating / 100

### NFL Win Probability
- **Normal CDF spread model**: σ = 13.45 pts (historical fit)
- **TD probability**: `P(TD) = 1 − e^(−RZ_usage × 0.40)`
- **Yardage props**: Normal approximation on season averages

### Value & Kelly Sizing
- **Edge** = model probability − implied market probability
- **Quarter-Kelly** fraction for bet sizing
- Signals ranked by confidence tier then raw probability
