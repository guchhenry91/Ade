"""Central configuration for the Sports Betting Model."""

# ── League identifiers ──────────────────────────────────────────────────────
LEAGUES = {
    # Soccer
    "EPL":  {"sport": "soccer", "espn_slug": "eng.1",  "api_football_id": 39,  "name": "Premier League"},
    "UCL":  {"sport": "soccer", "espn_slug": "uefa.champions", "api_football_id": 2,  "name": "Champions League"},
    "LIGA": {"sport": "soccer", "espn_slug": "esp.1",  "api_football_id": 140, "name": "La Liga"},
    "L1":   {"sport": "soccer", "espn_slug": "fra.1",  "api_football_id": 61,  "name": "Ligue 1"},
    # Basketball
    "NBA":  {"sport": "basketball", "espn_slug": "nba", "name": "NBA"},
    # American Football
    "NFL":  {"sport": "americanfootball", "espn_slug": "nfl", "name": "NFL"},
}

# ── ESPN API (no key required) ───────────────────────────────────────────────
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports"

# ── Ball Don't Lie – NBA (free tier, no key needed for basic) ────────────────
BDL_BASE = "https://api.balldontlie.io/v1"
BDL_API_KEY = ""          # optional – set env var BALLDONTLIE_KEY for higher rate limits

# ── API-Football (free tier: 100 req/day) ────────────────────────────────────
API_FOOTBALL_KEY = ""     # set env var API_FOOTBALL_KEY
API_FOOTBALL_HOST = "v3.football.api-sports.io"
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# ── Model parameters ─────────────────────────────────────────────────────────
POISSON_GOAL_LAMBDA_CLIP = (0.3, 5.0)   # clip xG lambda for stability
SHOT_ATTEMPT_HOME_BOOST   = 1.05        # home-field shot volume boost
MIN_EDGE_PCT              = 2.0         # minimum edge % to flag as value bet
CONFIDENCE_THRESHOLDS = {
    "HIGH":   75,   # ≥75 %
    "MEDIUM": 55,   # 55-75 %
    "LOW":    0,    # <55 %
}

# ── Current season / year ─────────────────────────────────────────────────────
CURRENT_SEASON = {
    "soccer": 2024,
    "NBA":    "2024-25",
    "NFL":    2024,
}
