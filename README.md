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
- **IMF World Economic Outlook**: inflation, government debt and budget balance, current
  account, unemployment and growth, with the IMF's projections about five years ahead
- **Long-run data**: Maddison Project GDP per capita back to year 1, and the Penn World Table
  (output, capital, schooling, productivity since 1950) — two-country comparisons with
  overtaking and divergence years, and growth accounting
- **FRED**: any US macroeconomic series
- **Opportunity Atlas**: upward mobility and local characteristics for every US county
- **A paper library**: 26 papers — development economics (cash transfers, microcredit,
  deworming, graduation programs, education, poverty measurement, growth, migration) and US
  mobility research — searched with hybrid keyword + semantic retrieval and a local reranker,
  cited by author and year
- **J-PAL**: summaries of 1,318 randomized evaluations ("what works" evidence), with
  countries, samples, and results
- **DHS household surveys**: health, nutrition, fertility, and education for ~90 developing
  countries, including values by region within a country
- **Global Data Lab**: human development (HDI, life expectancy, schooling, income) for 1,805
  regions in 188 countries, 1990-2023
- **OpenAlex**: published research beyond the library (~250M works), also used to check
  that cited papers exist

For calculations the tools don't cover (growth needed to reach a target, projections,
regressions, population-weighted averages), the model can write a short Python script that
runs on the fetched data in a sandbox with no network access; its printed numbers are
fact-checked like any other.

Each answer also comes with charts of the data it used — time series as line charts, regions
and rankings as bar charts, world maps when many countries are involved, and an animated
Gapminder-style bubble chart when two indicators are compared over time. They're drawn by
[Ask](vendor/ask) (a small charting language, no AI model involved) and saved in `charts/`.

Economics conventions are enforced in code, not left to the model: poverty figures carry
their survey year and the current $3.00/day (2021 PPP) line, GDP per capita comes with both
market-rate and PPP values, current vs constant prices are flagged, newer data is flagged
when the model asks for an old year, and correlations are stated with their direction in
words.

**Reports** (in the web page's Reports tab, or from the command line). Python fetches every number
and builds every table; the model writes only a short summary, which is fact-checked (untick "Write
a summary" and Python lists the highlights instead, for free). Download as PDF, Word, Markdown,
LaTeX (with BibTeX), or the data itself.
- **Compare countries**: 2-6 countries on income, growth, poverty, health, education and more (~2-5K Groq tokens).
- **Country brief**: one country against its income group and region; long-run growth, the World
  Bank's growth forecast, differences between regions, and J-PAL studies and library papers about it (~3-5K).
- **Poverty profile**: the $3.00, $4.20 and $8.30 lines for every survey year, the number of poor,
  how old and how far apart the surveys are, and income by region (~2-4K).
- **What works**: randomized evaluations of an intervention from J-PAL, grouped by outcome and
  region with their results quoted, plus reviews from OpenAlex and passages from the library. The
  summary may cite only those sources, and every citation is checked (~4-6K).

The token figures can double if the fact-check asks the model for one fix. Sections whose data
isn't set up on a computer (long-run data, Global Data Lab, J-PAL, the paper library) are left out
with a note.

Online, answers come from a hosted open model (Groq, free tier). Offline, a local pipeline
(Ollama + `phi3.5`) answers from the paper library.

**How it does**: on a benchmark scored by code, 27/30 (Oct 6 2026) and 27/28 on the latest
version (Oct 7; the one miss was a grading quirk on a correct answer); a general chatbot without
web search scored 2/25 on the same questions, mostly from outdated numbers.

## Getting started

You run your own copy, on your own computer, with your own free API keys. Nothing is paid.
macOS works fully; on Linux everything works except `run_python` (custom calculations), which
needs the macOS sandbox and switches itself off elsewhere. You need Python 3.11+ (3.13
recommended) and about 3 GB of disk; 8 GB of RAM is enough.

```bash
git clone https://github.com/bobwrd/EconRAG.git
cd EconRAG
python3 setup_assistant.py            # shows what's set up (changes nothing)
python3 setup_assistant.py install    # walks through each missing step, asking first
```

The setup script asks before every download and says where it comes from and how big it is.
Only the first two steps are needed to start; each later one adds a data source or feature,
and whatever is missing is switched off (the web page's **Setup** panel shows what's on).

| Step | What it adds | Download / time |
|---|---|---|
| `packages` | Python packages in `.venv` (PyTorch, sentence-transformers, ...) | ~1.1 GB, a few minutes |
| `keys` | `.env` with free keys: [Groq](https://console.groq.com/keys) (needed for the online analyst), [FRED](https://fred.stlouisfed.org/docs/api/api_key.html), [OpenRouter](https://openrouter.ai/keys) (backup) | — |
| `atlas` | Opportunity Atlas county data + Census county list | ~3 MB |
| `papers` | the 26-paper library (open-access PDFs from their authors' sites; one needs a browser download) | ~115 MB |
| `index` | the paper search index (`ingest.py`) | ~2 min; 130 MB model |
| `models` | local reranker and embedding models (otherwise downloaded on first start) | ~1.7 GB |
| `longrun` | Maddison GDP per capita back to year 1 | <1 MB |
| `pwt` | Penn World Table 11.0 (needs the FRED key) | ~30 min |
| `jpal` | J-PAL's ~1,300 randomized-evaluation summaries (resumable) | ~90 min |
| `gdl` | Global Data Lab subnational HDI — free account, so by hand; the script shows how | ~13 MB |
| `charts` | PNG charts, world maps and animations via the bundled [Ask](vendor/ask) | ~300 MB |
| `offline` | answers without internet: [Ollama](https://ollama.com) + `phi3.5` | ~2.2 GB |

Run one step with `python3 setup_assistant.py install papers`. World Bank, DHS and OpenAlex
need no setup. Free tiers: Groq allows about 15 questions a day; OpenAlex about 100 searches.

## Use

```bash
.venv/bin/python web.py                          # web page at http://127.0.0.1:8765 (this computer only)
.venv/bin/python ask.py                          # or ask questions in the terminal
.venv/bin/python workflows.py compare Kenya Ghana Nigeria   # a report: PDF, Word, Markdown, LaTeX, data
.venv/bin/python workflows.py brief Kenya                    # country brief
.venv/bin/python workflows.py poverty India                  # poverty profile
.venv/bin/python workflows.py works cash transfers --region "South Asia"   # "what works" review
                                                             # (--no-summary on any of them: no Groq tokens)
.venv/bin/python longrun.py "South Korea" Ghana  # long-run comparison from the command line

.venv/bin/python tests/test_tools.py             # tool tests (no API usage)
.venv/bin/python tests/test_devecon.py           # development-economics formulas
.venv/bin/python tests/test_longrun.py           # long-run data
.venv/bin/python tests/test_dhs.py               # DHS surveys (live API)
.venv/bin/python tests/test_jpal.py              # J-PAL evaluations
.venv/bin/python tests/test_gdl.py               # Global Data Lab
.venv/bin/python tests/test_compute.py           # run_python sandbox
.venv/bin/python tests/test_web.py               # web UI (no API usage)
.venv/bin/python tests/test_setup.py             # setup script
.venv/bin/python tests/test_reports.py           # reports (World Bank data, no API usage)
.venv/bin/python tests/test_imf.py               # IMF World Economic Outlook (live data)

.venv/bin/python eval/benchmark.py run NAME                # 45-question benchmark (resumable;
                                                           # ~2 days of Groq's free tier)
.venv/bin/python eval/benchmark.py run NAME --only retest  # the 22 questions recent fixes target
.venv/bin/python eval/benchmark.py score NAME              # grade a run
.venv/bin/python eval/retrieval_eval.py --rerank --dev     # paper retrieval, no model needed
```

See `NOTES.md` for architecture, design decisions, and known pitfalls, and `ROADMAP.md` for
progress and what's next.
