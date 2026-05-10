#!/usr/bin/env python3
"""
slox_sb - Market Data Module
Free data sources only: yfinance, FRED, exchangeratesapi.io, FINRA TRACE.
Falls back to synthetic data with confidence tags.
"""

import json, random, time, logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("slox_sb.market_data")

CACHE_DIR = Path("/srv/slox_sb/data/market_data")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CONFIDENCE_LABELS = ["REALTIME", "DELAYED_15M", "CACHED_1H", "CACHED_1D", "STALE", "SYNTHETIC"]

# ── Simple in-memory cache ─────────────────────────────────────────
_cache = {}
def _get_cache(key, max_age_seconds=3600):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < max_age_seconds:
            return val, ts
    return None, 0

def _set_cache(key, val):
    _cache[key] = (val, time.time())

# ── FRED API (free tier, no key needed for basic data) ─────────────
def fetch_yield_curve():
    """Get US Treasury yield curve from FRED API."""
    try:
        import urllib.request, urllib.parse, json
        series = {
            "3m": "DGS3MO", "2y": "DGS2", "5y": "DGS5",
            "10y": "DGS10", "30y": "DGS30"
        }
        base = "https://api.stlouisfed.org/fred/series/observations"
        result = {}
        for label, series_id in series.items():
            params = urllib.parse.urlencode({
                "series_id": series_id,
                "api_key": "a6dde24cae4e3f5122a7ece5a9e1242b",  # Public FRED key
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1
            })
            url = f"{base}?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "slox_sb/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    obs = data.get("observations", [])
                    if obs and obs[0]["value"] != ".":
                        result[label] = float(obs[0]["value"])
            except Exception as e:
                logger.warning(f"FRED {series_id} failed: {e}")
        return result if result else None
    except Exception as e:
        logger.warning(f"FRED fetch failed: {e}")
        return None

def get_yields():
    """Get yield curve with caching. Returns dict with confidence."""
    cached, ts = _get_cache("yields", 1800)  # 30 min cache
    if cached:
        return cached
    data = fetch_yield_curve()
    if data:
        result = {
            "data": data,
            "confidence": "DELAYED_15M",
            "timestamp": datetime.utcnow().isoformat(),
            "source": "FRED"
        }
        _set_cache("yields", result)
        return result
    # Fallback to synthetic
    result = {
        "data": {"3m": 4.3, "2y": 3.9, "5y": 3.8, "10y": 4.1, "30y": 4.4},
        "confidence": "SYNTHETIC",
        "timestamp": datetime.utcnow().isoformat(),
        "source": "synthetic_fallback",
        "note": "FRED unavailable, using synthetic"
    }
    return result

# ── Synthetic equity data (free API fallback) ──────────────────────
def try_fetch_equity(ticker):
    """Try yfinance for a single ticker. Returns None on failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info
        if info and "regularMarketPrice" in info:
            return {
                "price": info.get("regularMarketPrice"),
                "change_pct": info.get("regularMarketChangePercent"),
                "volume": info.get("regularMarketVolume"),
                "market_cap": info.get("marketCap"),
                "div_yield": info.get("dividendYield"),
                "beta": info.get("beta"),
            }
    except ImportError:
        logger.warning("yfinance not installed, using synthetic")
    except Exception as e:
        logger.debug(f"yfinance {ticker} failed: {e}")
    return None

def get_equity_price(ticker):
    """Get equity price, falling back to synthetic."""
    data = try_fetch_equity(ticker)
    if data:
        return {"data": data, "confidence": "DELAYED_15M", "source": "yfinance",
                "timestamp": datetime.utcnow().isoformat()}
    return {"data": {"price": 100 + random.gauss(0, 5)}, "confidence": "SYNTHETIC",
            "source": "synthetic_fallback", "timestamp": datetime.utcnow().isoformat()}

# ── FX rates ───────────────────────────────────────────────────────
def get_fx_rates(base="USD"):
    """Get FX rates from exchangeratesapi.io or Frankfurter."""
    try:
        import urllib.request, json
        url = f"https://api.frankfurter.app/latest?from={base}"
        req = urllib.request.Request(url, headers={"User-Agent": "slox_sb/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            rates = data.get("rates", {})
            if rates:
                return {"data": rates, "confidence": "CACHED_1H",
                        "source": "frankfurter", "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        logger.debug(f"FX fetch failed: {e}")
    return {"data": {"EUR": 0.92, "GBP": 0.79, "CHF": 0.88, "JPY": 149.5, "SGD": 1.34},
            "confidence": "SYNTHETIC", "source": "synthetic_fallback",
            "timestamp": datetime.utcnow().isoformat()}

# ── Synthetic market scenario generator (for simulation engine) ────
SCENARIOS = {
    "bull": {"equity_return": 0.25, "bond_return": 0.02, "credit_spread_change": -0.002,
             "fx_vol": 0.06, "description": "Strong bull market, low vol"},
    "bear": {"equity_return": -0.25, "bond_return": 0.08, "credit_spread_change": 0.015,
             "fx_vol": 0.15, "description": "Bear market, flight to quality"},
    "inflation": {"equity_return": -0.05, "bond_return": -0.08, "credit_spread_change": 0.01,
                  "fx_vol": 0.12, "description": "Inflation shock, rates up"},
    "rate_hike": {"equity_return": -0.10, "bond_return": -0.05, "credit_spread_change": 0.008,
                  "fx_vol": 0.10, "description": "Aggressive rate hiking cycle"},
    "rate_cut": {"equity_return": 0.15, "bond_return": 0.06, "credit_spread_change": -0.005,
                 "fx_vol": 0.08, "description": "Easing cycle, risk-on"},
    "stagflation": {"equity_return": -0.15, "bond_return": -0.03, "credit_spread_change": 0.012,
                    "fx_vol": 0.14, "description": "Stagflation: low growth + high inflation"},
    "crisis": {"equity_return": -0.35, "bond_return": 0.12, "credit_spread_change": 0.025,
               "fx_vol": 0.20, "description": "Financial crisis, extreme vol"},
    "normal": {"equity_return": 0.08, "bond_return": 0.03, "credit_spread_change": 0.0,
               "fx_vol": 0.07, "description": "Normal growth, moderate vol"},
}

def get_scenario(name=None):
    if name and name in SCENARIOS:
        base = SCENARIOS[name].copy()
    else:
        base = random.choice(list(SCENARIOS.values())).copy()
    # Add random noise for variation
    for k in ["equity_return", "bond_return"]:
        base[k] = round(random.gauss(base[k], 0.03), 4)
    base["credit_spread_change"] = round(random.gauss(base["credit_spread_change"], 0.003), 4)
    base["fx_vol"] = round(random.gauss(base["fx_vol"], 0.01), 2)
    base["confidence"] = "SYNTHETIC"
    return base
