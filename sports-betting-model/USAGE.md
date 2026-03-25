# How to Use the Sports Betting Model

## 1. Installation

```bash
# Clone / navigate to the folder
cd sports-betting-model

# Install Python dependencies (Python 3.10+ required)
pip install -r requirements.txt
```

---

## 2. Quick start — offline demo (no API keys needed)

This runs everything with real 2025-26 season stats already hardcoded:

```bash
python demo.py
```

You'll see tables for every league and market printed in your terminal,
and a report saved to `reports/YYYY-MM-DD.md`.

---

## 3. Live data — API keys (optional but recommended)

| Key | What it unlocks | Where to get it (free) |
|-----|-----------------|------------------------|
| `API_FOOTBALL_KEY` | Soccer xG per fixture, player shot stats | https://dashboard.api-football.com (100 req/day free) |
| `BALLDONTLIE_KEY`  | NBA season averages, game logs | https://www.balldontlie.io |

```bash
export API_FOOTBALL_KEY=your_key_here
export BALLDONTLIE_KEY=your_key_here
python main.py          # now fetches live data automatically
```

---

## 4. CLI Commands

### Run everything today (all leagues)
```bash
python main.py
```

### Soccer only — choose your league
```bash
python main.py --sports soccer                   # all 4 leagues
python main.py --sports soccer --league EPL      # Premier League
python main.py --sports soccer --league UCL      # Champions League
python main.py --sports soccer --league LIGA     # La Liga
python main.py --sports soccer --league L1       # Ligue 1
```

### Analyse a specific soccer fixture
```bash
python main.py --fixture "Manchester City" "Arsenal" --league EPL
python main.py --fixture "Real Madrid" "Barcelona" --league LIGA
python main.py --fixture "PSG" "Marseille" --league L1
```
This generates: Match Winner · Over/Under 2.5 · BTTS · Correct Score (top 10) · Player Shots O/U · Anytime Scorer

### NBA — today's games
```bash
python main.py --sports nba
```

### NBA — player props (with known averages)
```bash
# Supply your own averages with --player-avgs (no API key needed)
python main.py --player-prop "Luka Doncic" \
  --lines "pts=34.5,reb=7.5,ast=8.5,3pm=4.5" \
  --player-avgs "pts=35.3,reb=7.5,ast=8.6,3pm=4.5"

python main.py --player-prop "Nikola Jokic" \
  --lines "pts=28.5,reb=12.5,ast=10.5,3pm=0.5" \
  --player-avgs "pts=28.2,reb=12.6,ast=10.5,3pm=0.6"
```

### NFL — today / current week
```bash
python main.py --sports nfl
```

### Only show value bets (positive edge vs market odds)
```bash
python main.py --value-only
python main.py --value-only --min-edge 3.0      # edge ≥ 3 %
```

### Save a markdown report
```bash
python main.py --save-report
# → reports/2026-03-22.md
```

### Combine flags
```bash
python main.py --sports soccer --league UCL --value-only --save-report
```

---

## 5. Markets explained

### Soccer markets

| Market | What it means | How to read it |
|--------|--------------|----------------|
| `MATCH_WINNER` | 1X2 – Home / Draw / Away | Model prob vs implied odds prob |
| `OVER_UNDER_GOALS` | Total goals over/under a line (default 2.5) | Poisson model on combined xG |
| `BTTS` | Both teams to score Yes/No | P(home scores) × P(away scores) |
| `CORRECT_SCORE` | Exact scoreline e.g. 1-0 / 2-1 / 0-0 | Poisson grid, top 10 shown |
| `PLAYER_SHOTS` | Player shots O/U per game | Normal approx on season avg |
| `ANYTIME_SCORER` | Player scores ≥1 goal | Bernoulli chain on shots × xG/shot |

#### Correct score example output
```
Man City 1–0 Arsenal    → 9.0%  odds 8.0  edge –3.5%
Man City 1–1 Arsenal    → 8.9%  odds 5.5  edge –9.3%
Man City 2–1 Arsenal    → 6.9%  odds 12.0 edge –1.4%
Arsenal 0–1 Man City    → 6.9%  odds 12.0 edge –1.4%
```
The most likely scoreline is shown first. When edge % is positive, the model
thinks the bookmaker is underpricing that scoreline.

### NBA markets

| Market | Key | What it means |
|--------|-----|---------------|
| `GAME_WINNER` | `pts` | Moneyline – home or away win |
| `TOTAL_POINTS` | — | Game total O/U |
| `PLAYER_POINTS_OU` | `pts` | Player points over/under |
| `PLAYER_REBOUNDS_OU` | `reb` | Player rebounds over/under |
| `PLAYER_ASSISTS_OU` | `ast` | Player assists over/under |
| `PLAYER_3PM_OU` | `3pm` | Three-pointers made over/under |
| `SEASON_AVG_*_OU` | — | Season-long stat over/under |

### NFL markets

| Market | What it means |
|--------|--------------|
| `GAME_WINNER` | Moneyline win probability |
| `SPREAD` | Against-the-spread cover probability |
| `TOTAL_POINTS` | Game total O/U |
| `ANYTIME_TD_SCORER` | Player scores any touchdown |
| `PLAYER_RUSHING_YARDS_OU` | Rushing yards O/U |
| `PLAYER_RECEIVING_YARDS_OU` | Receiving yards O/U |
| `PLAYER_PASSING_YARDS_OU` | Passing yards O/U |

---

## 6. Understanding the output columns

| Column | Meaning |
|--------|---------|
| **Model Prob** | Our model's probability for this outcome |
| **Mkt Odds** | Bookmaker decimal odds you supplied |
| **Edge %** | Model prob – implied prob. Positive = model thinks this is underpriced |
| **Kelly** | Suggested bet size as % of bankroll (quarter-Kelly, conservative) |
| **Conf** | HIGH (≥75%), MEDIUM (55-75%), LOW (<55%) |
| **Notes** | xG values, season averages, key context |

---

## 7. Supplying bookmaker odds for value detection

Without market odds, the model shows probabilities only (no edge calculation).
Pass odds to unlock edge % and Kelly sizing.

### Soccer fixture with full odds
```python
from models.football_model import FootballModel

model = FootballModel("EPL")
signals = model.analyze_fixture(
    "Liverpool", "Chelsea",
    market_odds={
        "winner": {"home": 1.85, "draw": 3.75, "away": 4.20},
        "ou25":   {"over": 1.85, "under": 2.00},
        "btts":   1.65,
        "correct_score": {
            "1-0": 6.50, "2-1": 7.50, "1-1": 5.50,
            "0-0": 9.00, "0-1": 10.0, "2-0": 8.50,
        },
    }
)
for s in signals:
    print(s.to_dict())
```

### NBA player prop with odds
```python
from models.nba_model import NBAModel

model  = NBAModel()
sigs   = model.player_props_by_name(
    "Luka Doncic",
    prop_lines = {"pts": 34.5, "reb": 7.5, "ast": 8.5, "3pm": 4.5},
    known_avgs = {"pts": 35.3, "reb": 7.5, "ast": 8.6, "3pm": 4.5},
    market_odds = {
        "pts": {"over": 1.91, "under": 1.91},
        "reb": {"over": 1.87, "under": 1.95},
        "3pm": {"over": 1.90, "under": 1.90},
    }
)
```

### NFL TD scorer
```python
from models.nfl_model import NFLModel

model = NFLModel()
sigs  = model.anytime_td_signals("21", "Bills vs Chiefs")   # ESPN team id
```

---

## 8. Using the model as a Python library

```python
# Import the pieces you need
from models.football_model import FootballModel
from models.nba_model      import NBAModel
from models.nfl_model      import NFLModel
from markets.value_engine  import filter_value_bets
from markets.report        import print_signals_table, save_markdown_report

# ── Soccer ──────────────────────────────────────────────────────────────────
model    = FootballModel("UCL")
signals  = model.run_today()              # live API fetch
# or
signals  = model.analyze_fixture("Real Madrid", "Bayern Munich")

# ── NBA ──────────────────────────────────────────────────────────────────────
nba      = NBAModel()
signals += nba.run_today()

# ── NFL ──────────────────────────────────────────────────────────────────────
nfl      = NFLModel()
signals += nfl.run_today()

# ── Filter + display ─────────────────────────────────────────────────────────
value    = filter_value_bets(signals, min_edge=2.0)
print_signals_table(value, title="Value Bets")
save_markdown_report(signals, outdir="reports")
```

---

## 9. Correct score — reading the table

Correct scores are low-probability events (typically 4–15% per scoreline).
Use them when a bookmaker's odds imply **less probability** than the model:

```
Model prob 9.0%  =  decimal fair odds of ~11.1
If bookmaker offers 13.0 on that scoreline → positive edge → potential value
```

The model ranks all scorelines 0–0 through 6–6 and shows the **top 10 most
likely**. The most common profitable patterns:
- **1-0 Home Win** — highest individual probability in most matches
- **0-0 Draw** — often overpriced by books in defensive matchups
- **2-1 Home Win** — popular result, sometimes underpriced after a 1-0 opener

---

## 10. Responsible use

- This model provides **probability estimates**, not guaranteed outcomes.
- Always verify odds against your bookmaker before placing bets.
- Use Kelly fractions as a guide only — never bet more than you can afford.
- Past performance of any statistical model does not guarantee future results.
