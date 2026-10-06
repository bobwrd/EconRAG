"""
Groq's OpenAI-compatible chat API (free tier), shared by ask.py and analyst.py,
with OpenRouter as a backup: when Groq can't serve a request for long (daily
token cap, outage, bad key), the same request goes to OpenRouter if
OPENROUTER_API_KEY is set — OPENROUTER_MODEL, default the same gpt-oss-120b.
Short per-minute rate limits still raise, so callers wait for Groq instead.
Every failure raises GroqUnavailable so callers can fall back to the local model.
"""

import os
import time

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
            return response
        except GroqUnavailable as e:
            if not (OPENROUTER_API_KEY and _wants_backup(e)):
                raise
            # don't retry Groq on every request until the daily cap clears
            _groq_blocked_until = time.time() + (e.retry_after or 15 * 60)
            why = "rate limit" if e.retry_after and "per day" not in str(e) else str(e)[:80]
            print(f"  (Groq {why}: switching to OpenRouter {OPENROUTER_MODEL})", flush=True)
    response = _post(OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL,
                     # only route to OpenRouter providers that support every parameter sent (tools)
                     {**payload, "provider": {"require_parameters": True}}, stream, "OpenRouter")
    last_provider = "openrouter"
    return response


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
