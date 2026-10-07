"""
Fetches live macroeconomic indicators from the FRED API.
Used by ask.py for live_data / both routes (get_indicator_summary), and by
analyst.py's tools for any of FRED's series (search_series, series_stats).
`python fred.py` sanity-checks the API key and network on its own.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")  # optional: without it only FRED data is off


def _key() -> str:
    """The key, or a clear error the callers already handle (the analyst shows it to the model;
    ask.py skips the live-data answer). Importing this module must never fail without it."""
    if not FRED_API_KEY:
        raise RuntimeError("no FRED_API_KEY in .env: FRED data is off on this computer "
                           "(free key: https://fred.stlouisfed.org/docs/api/api_key.html)")
    return FRED_API_KEY
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SEARCH_URL = "https://api.stlouisfed.org/fred/series/search"
FRED_SERIES_URL = "https://api.stlouisfed.org/fred/series"
# FRED's server-side transformations (https://fred.stlouisfed.org/docs/api/fred/series_observations.html#units)
UNITS = {
    "lin": "levels (no transformation)", "chg": "change from previous period",
    "ch1": "change from a year ago", "pch": "% change from previous period",
    "pc1": "% change from a year ago", "pca": "compounded annual rate of change (%)",
    "log": "natural log",
}
SPARK = "▁▂▃▄▅▆▇█"
TIMEOUT = 10         # seconds; without one, a stalled connection hangs ask.py forever
CACHE_TTL = 15 * 60  # FRED series update at most daily — no need to refetch per question

# The handful of indicators the assistant should be able to answer about.
# "units": "pc1" asks FRED to transform the raw series into year-over-year
# percent change server-side — without it, CPI returns a meaningless raw
# index level (e.g. 334.1) instead of an actual inflation rate.
INDICATORS = {
    "inflation rate (CPI, year-over-year % change)": {
        "series_id": "CPIAUCSL", "units": "pc1", "suffix": "%",
    },
    "real GDP": {
        "series_id": "GDPC1", "units": None, "suffix": " billion (chained 2017 dollars, annualized)",
    },
    "unemployment rate": {
        "series_id": "UNRATE", "units": None, "suffix": "%",
    },
    "federal funds rate": {
        "series_id": "FEDFUNDS", "units": None, "suffix": "%",
    },
}

# One pooled connection reused across calls (skips a TLS handshake per request),
# retrying transient failures (FRED occasionally 5xx's or rate-limits) instead
# of failing the whole question.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))))
_cache: tuple[float, str] | None = None


def get_latest_observation(series_id: str, units: str | None = None) -> tuple[str, str]:
    """Returns (date, value) for the most recent observation of a series."""
    params = {
        "series_id": series_id,
        "api_key": _key(),
        "file_type": "json",
        "sort_order": "desc",
        # A few rows, not 1: FRED marks missing observations with "." (e.g. a
        # newly-added period not yet published), so skip to the latest real one.
        "limit": 5,
    }
    if units:
        params["units"] = units
    response = _session.get(FRED_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    for obs in response.json()["observations"]:
        if obs["value"] != ".":
            return obs["date"], obs["value"]
    raise ValueError(f"FRED returned no recent values for {series_id}")


def _get(url: str, **params) -> dict:
    response = _session.get(url, params={**params, "api_key": _key(), "file_type": "json"},
                            timeout=TIMEOUT)
    if response.status_code == 400:  # FRED explains bad series ids / dates in the body
        raise ValueError(response.json().get("error_message", response.text[:200]))
    response.raise_for_status()
    return response.json()


def search_series(text: str, limit: int = 8) -> list[dict]:
    """FRED's own full-text search over its ~800K series, most popular first."""
    found = _get(FRED_SEARCH_URL, search_text=text, limit=limit,
                 order_by="popularity", sort_order="desc")["seriess"]
    return [{"series_id": s["id"], "title": s["title"], "units": s["units"],
             "frequency": s["frequency"], "seasonal_adjustment": s["seasonal_adjustment_short"],
             "data_through": s["observation_end"]} for s in found]


def sparkline(values: list[float], width: int = 40) -> str:
    """A text chart: one block character per point, height = value."""
    if len(values) > width:  # average into `width` buckets
        edges = [round(i * len(values) / width) for i in range(width + 1)]
        values = [sum(values[a:b]) / (b - a) for a, b in zip(edges, edges[1:])]
    lo, hi = min(values), max(values)
    return "".join(SPARK[int((v - lo) / (hi - lo) * (len(SPARK) - 1)) if hi > lo else 0]
                   for v in values)


def series_stats(series_id: str, start: str | None = None, end: str | None = None,
                 units: str = "lin") -> dict:
    """Fetches a series over [start, end] and computes its summary in Python,
    so the model reports measured numbers instead of estimating them."""
    if units not in UNITS:
        raise ValueError(f"units must be one of {list(UNITS)}")
    info = _get(FRED_SERIES_URL, series_id=series_id)["seriess"][0]
    params = {"series_id": series_id, "units": units}
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end
    obs = [(o["date"], float(o["value"])) for o in _get(FRED_URL, **params)["observations"]
           if o["value"] != "."]
    if not obs:
        raise ValueError(f"no observations for {series_id} in that date range")
    dates, values = zip(*obs)
    hi, lo = max(range(len(obs)), key=values.__getitem__), min(range(len(obs)), key=values.__getitem__)
    step = max(1, len(obs) // 24)  # ~24 evenly spaced points to show the shape
    return {
        "series_id": series_id, "title": info["title"], "frequency": info["frequency"],
        "units": info["units"] if units == "lin" else f"{UNITS[units]} of: {info['units']}",
        "seasonal_adjustment": info["seasonal_adjustment_short"],
        "observations": len(obs), "first": {"date": dates[0], "value": values[0]},
        "latest": {"date": dates[-1], "value": values[-1]},
        "max": {"date": dates[hi], "value": values[hi]}, "min": {"date": dates[lo], "value": values[lo]},
        "mean": round(sum(values) / len(values), 4),
        "change_first_to_latest": round(values[-1] - values[0], 4),
        "sampled_points (subsample: may miss peaks)": [[d, v] for d, v in obs[::step]][-25:],
        "sparkline": sparkline(list(values)),
    }


def get_indicator_summary() -> str:
    """Returns a plain-text block of the latest value for every tracked
    indicator, formatted to drop straight into a prompt as context."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < CACHE_TTL:
        return _cache[1]

    # The series are independent, so fetch them concurrently (~4x faster).
    with ThreadPoolExecutor(max_workers=len(INDICATORS)) as pool:
        observations = list(pool.map(
            lambda info: get_latest_observation(info["series_id"], info["units"]),
            INDICATORS.values(),
        ))
    lines = [
        f"- {name}: {value}{info['suffix']} (as of {date}, source: FRED series {info['series_id']})"
        for (name, info), (date, value) in zip(INDICATORS.items(), observations)
    ]
    summary = "\n".join(lines)
    _cache = (time.monotonic(), summary)
    return summary


if __name__ == "__main__":
    print(get_indicator_summary())
