# Development Economics Research Assistant

A research assistant for development economics (worldwide) and economic opportunity in the
US, built to answer with **exact, current, checkable numbers** rather than recalled ones.

Ask a question in plain English; a tool-using model decides what to look up, Python fetches
and computes every number, and an automatic fact-check verifies that each number and citation
in the answer traces back to what was actually retrieved.

**Sources**
- **World Bank** World Development Indicators: ~1,500 indicators for 217 economies, regions,
  and income groups (growth, poverty, inequality, health, education, labor, trade)
- **FRED**: any US macroeconomic series
- **Opportunity Atlas**: upward mobility and local characteristics for every US county
- **Your own library** of economics papers (PDFs), searched with hybrid keyword + semantic
  retrieval and a local reranker

Economics conventions are enforced in code, not left to the model: poverty figures carry
their survey year and poverty-line vintage, GDP per capita is labeled market vs PPP, current
vs constant prices are flagged, and correlations are stated with their direction in words.

Online, answers come from a hosted open model (Groq, free tier). Offline, a local pipeline
(Ollama + `phi3.5`) answers from the paper library.

## Setup

Requires Python 3.13 and, for offline answers, [Ollama](https://ollama.com) with `phi3.5`
pulled.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `.env` with your keys (free):

```
FRED_API_KEY=...      # https://fred.stlouisfed.org/docs/api/api_key.html
GROQ_API_KEY=...      # https://console.groq.com — optional; without it, local-only
```

**Data** (not in the repository):
- Put economics PDFs in `docs/`, then build the index: `.venv/bin/python ingest.py`
- Opportunity Atlas files in `data/atlas/`, from
  [opportunityinsights.org/data](https://opportunityinsights.org/data/):
  `county_outcomes_simple.csv` and `cty_covariates.csv`, plus the Census county list
  `national_county.txt` (www2.census.gov/geo/docs/reference/codes/files/national_county.txt)
- World Bank catalog: downloaded automatically into `data/worldbank/` on first use

## Use

```bash
.venv/bin/python ask.py                      # ask questions interactively
.venv/bin/python tests/test_tools.py         # tool tests (no API usage, ~5s)
.venv/bin/python eval/benchmark.py run NAME  # 31-question benchmark (resumable)
.venv/bin/python eval/benchmark.py score     # grade the latest run
```

See `NOTES.md` for architecture, design decisions, and known pitfalls, and `ROADMAP.md` for
what's next.
