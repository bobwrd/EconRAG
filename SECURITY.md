# Security

This assistant is built for **one person running their own copy on their own Mac**. It is not
safe to put on a server for other people as it is. This page says why, and what protects you
today.

## What it trusts and what it doesn't

| Part | Trusted? | Why it matters |
|---|---|---|
| You, at the keyboard | yes | the only user |
| Groq / OpenRouter (the online model) | partly | it writes Python code that `run_python` runs, and its answers are fact-checked by `verify.py` |
| Data sources (World Bank, IMF, FRED, DHS, OpenAlex, J-PAL) | partly | their numbers are shown as fetched; nothing they return is run as code |
| Other websites open in your browser | no | they could try to send requests to the local web page |
| Other computers on your network | no | they must not reach the web page at all |

## What protects you today

- **The web page only listens on your own computer** (`127.0.0.1`), so other computers can't
  reach it. It also refuses requests that name a different host (a trick called DNS rebinding)
  and form posts that come from another website (checked with the `Origin` header).
- **Model-written code runs in a sandbox** (`compute.py`, the `run_python` tool): macOS's
  `sandbox-exec` with no network access and no writing outside a temporary folder, 10 seconds of
  CPU, 15 seconds in all, and 3,000 characters of output. On computers without `sandbox-exec`
  (anything but macOS) the tool refuses to run rather than run code unprotected.
- **API keys live in `.env`**, which is in `.gitignore`, so they never reach GitHub.
- **Nothing is installed or downloaded without asking**: `setup_assistant.py` names each download,
  its source and size first.

## Known limits

- The sandbox is good enough for one person's own copy, not for strangers' code: `sandbox-exec`
  is deprecated by Apple, and the script can still read files your user account can read.
- `.env` is a plain text file. Anyone with access to your user account can read the keys.
- No limits on how often the web page can be used: fine for one person, not for a public site.

## If this ever goes on a server

Run `run_python` in a container or virtual machine instead of `sandbox-exec`, put the server
behind a login, keep keys in a secrets manager rather than `.env`, and add per-user rate limits
(Groq's free tier is 8,000 tokens a minute and 200,000 a day for the whole key).

## Reporting a problem

Open an issue on the GitHub repository, or for anything sensitive, contact the owner through
their GitHub profile.
