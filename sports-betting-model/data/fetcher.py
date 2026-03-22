"""
Generic HTTP fetcher with retry logic and caching.
All data-source modules use this as their HTTP client.
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

    for attempt in range(4):
        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if use_cache:
                cache_file.write_text(json.dumps(data))
            return data
        except requests.exceptions.HTTPError as e:
            logger.warning("HTTP %s for %s (attempt %d)", e.response.status_code, url, attempt + 1)
            if e.response.status_code in (429, 500, 502, 503):
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


def bdl_fetch(endpoint: str, params: Optional[Dict] = None) -> Optional[Any]:
    """Fetch from balldontlie.io NBA API."""
    key = os.getenv("BALLDONTLIE_KEY", "")
    headers = {"Authorization": key} if key else {}
    return fetch(f"https://api.balldontlie.io/v1/{endpoint}",
                 params=params, headers=headers)


def espn_fetch(sport: str, league: str, endpoint: str,
               params: Optional[Dict] = None) -> Optional[Any]:
    """Fetch from ESPN public API."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/{endpoint}"
    return fetch(url, params=params)
