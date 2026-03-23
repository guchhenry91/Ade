"""
Generic HTTP fetcher with retry logic and disk caching.
All data-source modules use this as their HTTP client.

NBA data: ESPN free API (no key required).
BDL (Ball Don't Lie) has been removed — was causing HTTP 429 rate limits.
"""
import os
import time
import json
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

CACHE_DIR  = Path(__file__).parent.parent / ".cache"
CACHE_TTL  = 3600   # seconds


def _cache_path(url: str, params: dict) -> Path:
    key = hashlib.md5(f"{url}{json.dumps(params, sort_keys=True)}".encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def fetch(url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None,
          use_cache: bool = True, timeout: int = 15) -> Optional[Any]:
    """
    Fetch JSON from *url* with optional disk cache.
    Returns parsed JSON or None on failure.
    On HTTP 429: returns None immediately — no retries (retrying a rate limit
    just makes it worse; callers should use a fallback data source).
    """
    params  = params or {}
    headers = headers or {}

    cache_file = _cache_path(url, params)
    if use_cache and cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL:
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass

    for attempt in range(3):
        try:
            _t0 = time.time()
            resp = requests.get(url, params=params, headers=headers,
                                timeout=timeout)
            _ms = (time.time() - _t0) * 1000
            if _ms > 800:
                logger.warning("[SLOW %.0fms] %s", _ms, url)
            resp.raise_for_status()
            data = resp.json()
            if use_cache:
                cache_file.write_text(json.dumps(data))
            return data
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status == 429:
                # Rate limited — return None immediately without retrying.
                # Callers should use their ESPN fallback instead.
                logger.warning("[RATE LIMIT 429] %s — returning None", url)
                return None
            logger.warning("HTTP %s for %s (attempt %d)", status, url, attempt + 1)
            if status in (500, 502, 503):
                time.sleep(2 ** attempt)
            else:
                break
        except Exception as e:
            logger.warning("Fetch error %s (attempt %d): %s", url, attempt + 1, e)
            time.sleep(2 ** attempt)

    return None


def api_football_fetch(endpoint: str, params: Optional[Dict] = None) -> Optional[Any]:
    """Fetch from api-football.com (requires API_FOOTBALL_KEY env var)."""
    key = os.getenv("API_FOOTBALL_KEY", "")
    if not key:
        logger.debug("API_FOOTBALL_KEY not set – skipping api-football call")
        return None
    headers = {
        "x-rapidapi-host": "v3.football.api-sports.io",
        "x-rapidapi-key":  key,
    }
    return fetch(f"https://v3.football.api-sports.io/{endpoint}",
                 params=params, headers=headers)


def espn_fetch(sport: str, league: str, endpoint: str,
               params: Optional[Dict] = None) -> Optional[Any]:
    """Fetch from ESPN public API (no key required, no rate limits)."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/{endpoint}"
    return fetch(url, params=params)
