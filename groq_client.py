"""
Groq's OpenAI-compatible chat API (free tier), shared by ask.py and analyst.py.
Every failure — offline, rate limit, bad key, retired model — raises
GroqUnavailable so callers can fall back to the local model.
"""

import os

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

_session = requests.Session()


# Free tier (Oct 2026): 8,000 tokens/minute and 1,000 requests/day for every model;
# gpt-oss-120b also has 200,000 tokens per rolling 24 hours (others unchecked). The daily token cap is NOT in the
# x-ratelimit-* headers; it only appears in the 429 error message.


class GroqUnavailable(Exception):
    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after  # seconds, when Groq rate-limited us


def post(payload: dict, stream: bool = False) -> requests.Response:
    """POSTs a chat completion (model filled in) and returns the 200 response."""
    try:
        response = _session.post(GROQ_URL, stream=stream, timeout=(5, 90),
                                 headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                                 json={"model": GROQ_MODEL, "stream": stream, **payload})
    except requests.RequestException as e:
        raise GroqUnavailable(f"can't reach Groq ({type(e).__name__})") from e
    if response.status_code != 200:
        try:
            reason = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            reason = response.text[:200]
        retry_after = response.headers.get("retry-after")
        raise GroqUnavailable(f"HTTP {response.status_code}: {reason}",
                              float(retry_after) if retry_after else None)
    return response
