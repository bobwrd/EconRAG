# Development Economics Research Assistant

A research assistant for development economics (worldwide) and economic opportunity in the
US, built to answer with **exact, current, checkable numbers** rather than recalled ones.

Ask a question in plain English; a tool-using model decides what to look up, Python fetches
and computes every number, and an automatic fact-check verifies that each number and citation
in the answer traces back to what was actually retrieved.

**Sources**
- **World Bank** World Development Indicators: ~1,500 indicators for 217 economies, regions,
  and income groups (growth, poverty, inequality, health, education, labor, trade), plus
  growth forecasts from Global Economic Prospects and comparisons with a country's income
  group and region
- **Long-run data**: Maddison Project GDP per capita back to year 1, and the Penn World Table
  (output, capital, schooling, productivity since 1950) — two-country comparisons with
  overtaking and divergence years, and growth accounting
- **FRED**: any US macroeconomic series
- **Opportunity Atlas**: upward mobility and local characteristics for every US county
- **A paper library**: 26 papers — development economics (cash transfers, microcredit,
  deworming, graduation programs, education, poverty measurement, growth, migration) and US
  mobility research — searched with hybrid keyword + semantic retrieval and a local reranker,
  cited by author and year
- **OpenAlex**: published research beyond the library (~250M works), also used to check
  that cited papers exist

Economics conventions are enforced in code, not left to the model: poverty figures carry
their survey year and the current $3.00/day (2021 PPP) line, GDP per capita comes with both
market-rate and PPP values, current vs constant prices are flagged, newer data is flagged
when the model asks for an old year, and correlations are stated with their direction in
words.

Online, answers come from a hosted open model (Groq, free tier). Offline, a local pipeline
(Ollama + `phi3.5`) answers from the paper library.

**How it does**: on a 30-question benchmark scored by code, 27/30 (Oct 2026); a general
chatbot without web search scored 2/25 on the same questions, mostly from outdated numbers.

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
- **Papers**: put PDFs in `docs/` (the development papers and their links are listed in
  `PAPER_PROPOSAL.md`; each PDF needs an author-year entry in `papers.json`), then build the
  index: `.venv/bin/python ingest.py`
- **Opportunity Atlas** files in `data/atlas/`, from
  [opportunityinsights.org/data](https://opportunityinsights.org/data/):
  `county_outcomes_simple.csv` and `cty_covariates.csv`, plus the Census county list
  `national_county.txt` (www2.census.gov/geo/docs/reference/codes/files/national_county.txt)
- **Long-run data** in `data/longrun/`:
  - Maddison: save
    [Our World in Data's CSV](https://ourworldindata.org/grapher/gdp-per-capita-maddison-project-database.csv?v=1&csvType=full&useColumnShortNames=true)
    as `owid_maddison_gdppc.csv` (or the official `mpd2023_web.xlsx`), then
    `.venv/bin/python longrun.py --import`
  - Penn World Table 11.0: `.venv/bin/python longrun.py --import-fred-pwt` (fetches FRED's
    copy, ~20 minutes; or save the official `pwt110.xlsx` and use `--import`)
- **World Bank** catalog: downloaded automatically into `data/worldbank/` on first use

## Use

```bash
.venv/bin/python ask.py                          # ask questions interactively
.venv/bin/python longrun.py "South Korea" Ghana  # long-run comparison from the command line

.venv/bin/python tests/test_tools.py             # tool tests (no API usage)
.venv/bin/python tests/test_devecon.py           # development-economics formulas
.venv/bin/python tests/test_longrun.py           # long-run data

.venv/bin/python eval/benchmark.py run NAME                # 45-question benchmark (resumable;
                                                           # ~2 days of Groq's free tier)
.venv/bin/python eval/benchmark.py run NAME --only retest  # the 22 questions recent fixes target
.venv/bin/python eval/benchmark.py score NAME              # grade a run
.venv/bin/python eval/retrieval_eval.py --rerank --dev     # paper retrieval, no model needed
```

See `NOTES.md` for architecture, design decisions, and known pitfalls, and `ROADMAP.md` for
progress and what's next.
