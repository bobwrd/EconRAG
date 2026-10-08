"""
Groq's OpenAI-compatible chat API (free tier), shared by ask.py and analyst.py,
with OpenRouter as a backup: when Groq can't serve a request for long (daily
token cap, outage, bad key), the same request goes to OpenRouter if
OPENROUTER_API_KEY is set — OPENROUTER_MODEL, default the same gpt-oss-120b.
Short per-minute rate limits still raise, so callers wait for Groq instead.
Every failure raises GroqUnavailable so callers can fall back to the local model.

Token use: each reply's token counts (already in every reply, nothing extra is sent)
are added to data/usage/<date>.jsonl; usage() totals the last 24 hours against
Groq's daily cap. `python groq_client.py` prints it.
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
# Groq retired its Llama models (Oct 2026); list what a key can use with
# GET https://api.groq.com/openai/v1/models. gpt-oss-120b is a reasoning model:
# its hidden reasoning is returned separately from the answer but counts
# against max_tokens.
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-120b")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Groq errors that won't clear within a short wait: hand the request to OpenRouter.
BACKUP_AFTER_WAIT = 10  # seconds; only short per-minute waits stay on Groq, longer ones go to OpenRouter
last_provider = "groq"   # which service answered the latest request
_groq_blocked_until = 0.0  # after a daily-cap 429, skip Groq until then

_session = requests.Session()


def notify(text: str):
    """Status messages for the user; web.py replaces this to show them on the page."""
    print(text, flush=True)


USAGE_DIR = Path(__file__).resolve().parent / "data" / "usage"
DAILY_TOKEN_LIMIT = 200_000  # gpt-oss-120b on Groq's free tier, per rolling 24 hours

# Free tier (Oct 2026): 8,000 tokens/minute and 1,000 requests/day for every model;
# gpt-oss-120b also has 200,000 tokens per rolling 24 hours (others unchecked). The daily token cap is NOT in the
# x-ratelimit-* headers; it only appears in the 429 error message.


class GroqUnavailable(Exception):
    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after  # seconds, when Groq rate-limited us


def _wants_backup(e: GroqUnavailable) -> bool:
    text = str(e)
    return ("per day" in text or "can't reach" in text or text.startswith(("HTTP 401", "HTTP 403", "HTTP 5"))
            or (e.retry_after or 0) > BACKUP_AFTER_WAIT)


def post(payload: dict, stream: bool = False) -> requests.Response:
    """POSTs a chat completion (model filled in) and returns the 200 response:
    Groq first, OpenRouter when Groq is out for a while (see the module docstring)."""
    global last_provider, _groq_blocked_until
    if not (OPENROUTER_API_KEY and time.time() < _groq_blocked_until):
        try:
            response = _post(GROQ_URL, GROQ_API_KEY, GROQ_MODEL, payload, stream, "Groq")
            last_provider = "groq"
            _record(response, stream)
            return response
        except GroqUnavailable as e:
            if not (OPENROUTER_API_KEY and _wants_backup(e)):
                raise
            # don't retry Groq on every request until the daily cap clears
            _groq_blocked_until = time.time() + (e.retry_after or 15 * 60)
            why = "rate limit" if e.retry_after and "per day" not in str(e) else str(e)[:80]
            notify(f"  (Groq {why}: switching to OpenRouter {OPENROUTER_MODEL})")
    response = _post(OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL,
                     # only route to OpenRouter providers that support every parameter sent (tools)
                     {**payload, "provider": {"require_parameters": True}}, stream, "OpenRouter")
    last_provider = "openrouter"
    _record(response, stream)
    return response


# ------------------------------------------------------------------ token use
def _record(response: requests.Response, stream: bool):
    """Adds the reply's token counts to today's file. Streamed replies (eval/run_eval.py
    only) carry no counts and aren't recorded; a failure here never fails the request."""
    if stream:
        return
    try:
        record_usage(last_provider, response.json().get("usage") or {})
    except (ValueError, OSError):
        pass


def record_usage(provider: str, usage: dict, now: datetime | None = None, folder: Path | None = None):
    now = now or datetime.now()
    folder = folder or USAGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    line = {"time": now.isoformat(timespec="seconds"), "provider": provider,
            "prompt": usage.get("prompt_tokens", 0), "completion": usage.get("completion_tokens", 0),
            "total": usage.get("total_tokens", 0)}
    with (folder / f"{now:%Y-%m-%d}.jsonl").open("a") as f:
        f.write(json.dumps(line) + "\n")


def usage(now: datetime | None = None, folder: Path | None = None) -> dict:
    """Tokens and requests in the last 24 hours, per provider, and what's left of Groq's cap.
    Counts only what this copy sent: other programs using the same key aren't seen."""
    now = now or datetime.now()
    folder = folder or USAGE_DIR
    since = now - timedelta(hours=24)
    out = {"groq": 0, "openrouter": 0, "requests": 0}
    for day in sorted({since.date(), now.date()}):
        path = folder / f"{day:%Y-%m-%d}.jsonl"
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            try:
                line = json.loads(raw)
                if datetime.fromisoformat(line["time"]) > since:
                    out[line["provider"]] = out.get(line["provider"], 0) + line["total"]
                    out["requests"] += 1
            except (ValueError, KeyError):
                continue
    out["limit"] = DAILY_TOKEN_LIMIT
    out["groq_left"] = max(0, DAILY_TOKEN_LIMIT - out["groq"])
    return out


if __name__ == "__main__":
    u = usage()
    print(f"Last 24 hours: {u['groq']:,} Groq tokens of {u['limit']:,} ({u['groq_left']:,} left), "
          f"{u['openrouter']:,} OpenRouter tokens, {u['requests']} requests.\n"
          "Counts only this copy's requests (data/usage/).")


def _post(url: str, key: str | None, model: str, payload: dict, stream: bool, name: str) -> requests.Response:
    try:
        response = _session.post(url, stream=stream, timeout=(5, 90),
                                 headers={"Authorization": f"Bearer {key}"},
                                 json={"model": model, "stream": stream, **payload})
    except requests.RequestException as e:
        raise GroqUnavailable(f"can't reach {name} ({type(e).__name__})") from e
    if response.status_code != 200:
        try:
            reason = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            reason = response.text[:200]
        retry_after = response.headers.get("retry-after")
        raise GroqUnavailable(f"HTTP {response.status_code}: {reason}" + ("" if name == "Groq" else f" ({name})"),
                              float(retry_after) if retry_after else None)
    return response
