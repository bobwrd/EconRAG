# Roadmap: a development-economics research assistant

**Goal:** something anyone (students, journalists, analysts, researchers) would use for
development-economics questions *instead of* general chatbots like ChatGPT.

**Positioning.** General chatbots write and reason better than a free open model, and that
won't change. This project wins on what they can't do reliably:

| What users need | General LLMs | This project |
|---|---|---|
| Exact, current numbers (poverty, GDP, health, education) | Often outdated or guessed | Fetched live from the source, computed in Python, fact-checked |
| Provenance | Vague | Every number names its source, series, and year |
| Reproducibility | No | Every answer and report exports its data (zip: full series + what was fetched); run_python calculations show their script |
| Development-economics conventions | Inconsistent | Encoded in code: PPP vs market rates, poverty-line vintages, GDP vs GNI, survey years |
| "What works" evidence | Blends real and invented studies | Grounded in RCT/evaluation databases; citations verified to exist |
| Plain explanations for non-economists | Good | Kept: a plain-language answer first, technical detail on request |

**Design principles** (learned the hard way; see NOTES.md gotchas #7-9):
1. **The model chooses, Python computes.** Tools return computed results, never raw data for
   the model to summarize.
2. **Few general tools over a large data catalog**, not one tool per source. Tool schemas are
   re-sent with every request (8K tokens/minute on Groq's free tier) and every extra tool is
   another chance to pick the wrong one.
3. **Judgment in code, not prompts.** "Is this difference meaningful", "which direction is
   this correlation", "is this citation real": computed and checked by code.
4. **Measure before and after every phase** (Phase 0's benchmark).

---

## Status (Oct 7 2026)

| Phase | State |
|---|---|
| 0. Measure | Done. 27/30 on Oct 6; 27/28 on Oct 7 (28 of the 45 questions run, then closed) |
| 1. Core data | Done |
| 2. Computation | Done: `run_python` (sandboxed), `devecon.py`, automatic charts (no tokens) |
| 3. Literature | Done: development library (15 papers), OpenAlex + citation checks, J-PAL's 1,318 evaluation summaries |
| 4. Long-run / subnational | Done: Maddison, Penn World Table 11.0, DHS surveys (with regions), Global Data Lab subnational HDI |
| 5. Workflows | Done: compare countries, country brief, poverty profile, "what works" (`workflows.py`, `report_recipes.py`, web Reports tab, PDF/Word/Markdown/LaTeX/BibTeX/data) |
| 6. Interface | Done: local web UI (`web.py`) and a setup script for other people's own copies (`setup_assistant.py`) |

The Oct 6 work (benchmark fixes, peer groups, `devecon.py`, `longrun.py`, the development library,
multi-column PDF extraction) is merged into `main` and was benchmarked on Oct 7 (27/28, below).

## Phase 0: Measure cheaply

Groq's free tier allows 1,000 requests/day, 8,000 tokens/minute, **and 200,000 tokens per
rolling 24 hours** for gpt-oss-120b (that cap isn't in the response headers — it surfaced
when a day of testing used it up). At ~10-15K tokens per analyst question, one benchmark run
(31 questions) spans about two days of budget; `benchmark.py run` stops cleanly at the cap
and resumes where it left off.

- [x] **0a. Tool tests (zero LLM tokens)** — `tests/test_tools.py` (43 tests), plus
      `tests/test_devecon.py` (28) and `tests/test_longrun.py` (15 + 1 skipped until PWT is
      present), run after every change. Usually seconds; `test_tools` occasionally takes ~3 min,
      probably FRED rate-limit retries (not confirmed). Also tests the analyst loop against a scripted fake Groq. First catch: the
      number fact-check accepted 100% of random 1-decimal numbers (any-pair arithmetic over
      ~150 tool numbers); now only arithmetic on numbers shown in the answer counts (6%).
- [x] **0b. Mini benchmark, auto-scored by code (no LLM judge)** —
      `eval/benchmark.py` + `eval/benchmark_questions.json`. **Baseline (Oct 6 2026, main): 27/30**
      (one question not yet run; 26/30 before a grading-pattern fix). On the 25 questions a general
      chatbot also answered: 23/25 vs 2/25. Fit in one day's budget (~10K tokens/question). Real
      failures: a stale year the model passed itself (Niger), a market-rate-only answer to a PPP trap
      (India), a paper's own figure not quoted (race gap); plus 4 false "not verified" flags on
      correct answers. All fixed in code, now in `main` (not yet re-measured).
      14 harder questions added for the next phases (31 → 45): 5 `compute` (growth rates,
      doubling time, ratios, population-weighted averages, peer groups), 5 `dev_lit`
      (microcredit, deworming, cash transfers, graduation, the $3.00 line), 4 `longrun`.
      **Oct 7 2026 (`oct7`, main with reports and IMF): 27/28** — data 14/14, US tools 5/5, traps
      6/7, calculations 2/2. Stopped at question 29 when the free daily limits ran out; the user
      closed the benchmark there (dev_lit, longrun and 3 compute questions not run). The one fail
      (India PPP vs market rates) gave both figures but wrote "market‑exchange" with a non-breaking
      hyphen the grading pattern doesn't match.
      - ~15 data questions, answers computed directly from source data by a script, so
        they stay correct when the data updates
      - ~5 literature questions with an expected-fact pattern (like `eval/questions.json`)
      - ~10 **trap questions** that general LLMs get wrong, e.g.:
        poverty at $2.15 (2017 PPP) vs the current line (2021 PPP) · GDP per capita at market
        exchange rates vs PPP · GDP vs GNI · "the poverty rate" when the latest survey is
        years old · nominal vs real growth · headcount ratio vs number of poor people ·
        employment rate vs 100 − unemployment
      - ~120-150 Groq requests and ~300-450K tokens: about two days of the free budget.
- [x] **0c. General-chatbot comparison at zero API cost** (user): 2/25 strict. Questions in
      `eval/external/questions.md`; paste answers into `eval/external/<chatbot>.md` under the
      same `## id` headings; grade with `benchmark.py score-external <chatbot>`.
- ~~0d. Smoke set~~ — dropped: 0a covers quick checks for free; run the full benchmark at
  the end of each phase.

## Phase 1: Core development data — built Oct 2026; benchmarked (dev_data 13/14 on main)

Three general tools replace today's FRED-only ones:
`search_data(query)` → a local catalog (series descriptions embedded with the existing
retrieval code), `get_data(source, series, countries, years)` → computed stats per country,
`describe(series)` → definition, units, coverage, caveats.

- [x] **World Bank WDI** (API, no key): 1,498 indicators × 217 economies + aggregates,
      via `search_data` / `get_data` (`worldbank.py`). 10/10 core indicators found first by
      search in tests.
- [x] **Poverty & inequality**: WDI carries PIP's series. Current line verified from the API:
      **$3.00/day, 2021 PPP** (`SI.POV.DDAY`); results carry a survey-year caveat.
- [x] **Forecasts**: World Bank Global Economic Prospects growth forecasts (June 2026 edition,
      through 2028, 143 economies + regions), labeled FORECAST with the edition date. Plus the
      **IMF World Economic Outlook** (`imf.py`, Oct 2026; April 2026 edition): inflation,
      government debt and budget balance, current account, unemployment, growth, with projections
      to 2031, as `source="imf"` in chat and an "IMF outlook" section in the country brief.
- [x] **Our World in Data** (CSV downloads): used for Maddison GDP per capita (Phase 4); no
      other series planned (closed Oct 7 2026).
- [x] **Country handling in code**: names, ISO codes, aliases ("Ivory Coast" → CIV,
      "DRC" → COD), typo tolerance, regions and income groups; "Congo" rejected as ambiguous.
      Peer groups: `get_data(peers=true)` adds the income-group and region aggregates plus the
      country's rank/median/percentile among members (computed in Python).
- [x] FRED and the Opportunity Atlas kept as modules (FRED now via the same two tools).

## Phase 2: Computation sandbox

- [x] `run_python` tool (`compute.py`, Oct 2026): the model lists the data it needs
      (worldbank / longrun / gdl / fred specs) and a script; Python fetches the data, then runs
      the script under macOS `sandbox-exec` — no network, writes only to its temp folder, 10s
      CPU / 15s wall clock, 3,000-character output cap. The script gets `DATA[series][ISO3] =
      {year: value}`, `devecon`, numpy, scipy.stats (no pandas/statsmodels: not installed, not
      needed so far). Fine for personal use; **not** a security boundary for other users. Phase 6
      chose a localhost-only UI and own copies, not a public site; if it's ever made public, move
      this into a container or VM first. Off (refuses to run) where macOS's sandbox is missing.
- [x] `devecon.py` helper library (28 tests, no network), built Oct 2026; available inside run_python (not a tool of its own): PPP conversion, constant-price rebasing, CAGR,
      poverty gap / squared gap, Gini and Lorenz from distributions, per-capita, income-group
      averages (population-weighted vs simple), latest-available-year logic.
- [x] Numbers the script prints count as tool results for `verify.py` (automatic: every
      non-passage tool result is evidence).
- [x] Charts (`charts.py`, Oct 2026), drawn by plain Python — not a model tool, so no tokens
      and nothing added to requests (tool schemas are at their 2,000-token budget: 1,994).
      After each answer, every time series fetched (World Bank, FRED, long-run, DHS surveys)
      becomes a line chart and every regional breakdown or ranking (Global Data Lab, DHS) a bar
      chart, with the source written on it. Drawn by **Ask** (the user's charting language, a
      copy in `vendor/ask` with its own environment: pandas, matplotlib, geopandas, 295 MB) as
      PNG, plus world **maps** when a result covers 5+ countries and an **animated bubble
      chart** (GIF, Gapminder-style, sized by population) when an answer used two World Bank
      indicators for the same countries. All of an answer's charts are drawn in one Ask process
      (~4.5 s for 7 charts). Without Ask, line and bar charts fall back to plain SVG.
      Also charted: county rankings, county profiles (mobility by group: county vs state vs
      US), county correlations (average y by fifth of x, the Opportunity Insights binned
      scatter), growth accounting, peer comparisons, and small tables printed by run_python
      ("label: value" lines). Axes use readable names (backticked columns in Ask). Still not
      possible: asking for a specific chart (the model doesn't control charts, by design).

## Phase 3: Literature and evidence

- [x] **Development library** (Oct 2026): all 15 approved open-access papers in `docs/`
      (`PAPER_PROPOSAL.md`: microcredit, cash transfers, deworming, graduation, UBI, education
      "smart buys", gender, poverty measurement and the $3.00 line, institutions, growth,
      structural change, migration), 26 papers / 2,212 chunks in all. Passages carry author-year
      and title from `papers.json`, so citations are real references, not file names.
      Multi-column extraction added to `ingest.py` (original papers extract byte-identical).
      Retrieval, evidence in the top 5 after reranking: development questions in everyday words
      7/11 (`retrieval_eval.py --dev`, measured before the 15th paper was added); original
      questions 16 -> 15/18 (one fact pushed from rank 15 to 19 by the larger corpus); reworded
      6/12 unchanged. Known gap: the ODI review's sidebar text is glued onto ~1/3 of its lines.
- [x] **OpenAlex** (free, ~250M works): `search_literature` tool (abstracts count as
      evidence for the fact-check) and citation existence checks: unsupported citations are
      labeled "real, not retrieved" vs "likely invented" in the revision feedback. Built Oct
      2026; in use with live Groq since.
- [x] **"What works" evidence: J-PAL** (`jpal.py`, Oct 2026): 1,318 of the 1,321 evaluation summaries
      (3 pages return server errors on J-PAL's side; listed via the sitemap; robots.txt allows
      /evaluation/), ~90 min once at one page a
      second into `data/jpal/evaluations.json`. Tool `search_evaluations`: BM25 over title,
      sector, intervention, outcomes, countries (detected from the text — the pages have no
      country field) and results; returns researchers, countries, timeline, sample, and the
      first 160 words of "Results and policy lessons", which count as evidence for the
      fact-check. Cost: ~170 tokens per request (schema + prompt line).
- [x] Embedding model upgraded to `bge-small-en-v1.5`: better candidates (reworded MRR
      .148 → .262) but no end-to-end gain after Laya's rerank (14/18 and 5-6/12 either way).
      The reranker was then the bottleneck: fusion constant 60 -> 10 lifted evidence in the
      top 5 after reranking 14 -> 16/18 and 5 -> 6/12 (NOTES.md "Reranking").

## Phase 4: Subnational and long-run data

- [x] **Global Data Lab** (`gdl.py`, Oct 2026): Subnational HDI database v10.2 — HDI, health,
      education, and income indices, life expectancy, expected and mean years of schooling,
      GNI per capita (2021 PPP; verified against UNDP: USA $73,644 vs $73,650), population, by
      sex — for 1,805 regions in 188 countries, 1990-2023. Downloads need a free account, so the
      user saves the CSV into `data/gdl/` by hand. `source="gdl"`: a country's national value
      and every region (highest/lowest, spread, biggest gain since `start`; top and bottom 6
      shown), or `["all"]` to rank regions worldwide. Regions follow survey boundaries (Kenya:
      8 former provinces; DHS has the 47 counties).
- [x] **DHS Program indicators API** (`dhs.py`, Oct 2026; open API, no key): 3,257
      indicators, ~90 countries, as `source="dhs"` in search_data/get_data. Headline value
      per survey (the API's preferred reference period), every survey with year and type
      (DHS/MIS/AIS), change over time, and `regions=true` for subnational values: highest/
      lowest region, spread, median, regions whose confidence interval clears the national
      value (shown to the model as the top and bottom 6 regions — Kenya has 47 counties).
- [x] **Maddison Project 2023**: live via Our World in Data's CSV (GDP per capita back to year
      1; no population). `longrun.py`, `source="longrun"`: per-country stats, two-country
      comparison with ratios, overtaking and divergence years (e.g. South Korea passed Ghana in
      1967). Tests check exact values from the file.
- [x] **Penn World Table 11.0** via FRED (`longrun.py --import-fred-pwt`, Oct 2026): the
      official host (dataverse.nl) serves a bot check that times out for scripts and browsers,
      but FRED republishes the full 11.0 release ("Penn World Table 11.0", data to 2023, 2021
      US$): 1,902 series, 167 of PWT's 185 countries, 10,279 country-years, ~30 min one-time
      import at FRED's rate limit. FRED's country code is ISO2 + "A" (KRA = Korea), mapped via
      the World Bank's country list. Growth accounting works, e.g. South Korea 1960-2019: output
      per worker 4.55%/yr = capital 2.50 + schooling 0.72 + TFP 1.34 (PWT's own TFP: 1.46).
      Note PWT and Maddison can disagree (Korea passes Ghana in 1975 in PWT's rgdpe, 1967 in
      Maddison); results name their source. Not yet in the benchmark.

## Phase 5: Workflows people repeat

Multi-step templates built on the same tools, exportable as Markdown/LaTeX with BibTeX plus
data (decided Oct 2026: a fixed Python recipe per report — Python gathers data and writes every table,
one Groq request writes the summary, verify.py checks it; exports PDF, Word, Markdown, LaTeX,
BibTeX, data zip; no reproduce script for now):
- [x] **Engine + compare countries** (`workflows.py compare`, Oct 2026): 2-6 countries x topic
      bundles (income, growth, poverty, health, education, infrastructure, jobs, population,
      macro) + extra indicators; average growth computed in Python; summary ~2-5K tokens, or
      free (a "Write a summary" checkbox: unticked, Python lists highest/lowest per indicator).
- [x] **Country brief** (`report_recipes.country_brief`, Oct 2026): income, growth, poverty,
      health, education against the income group and region (aggregates + rank), 10-year growth,
      GEP forecast, Maddison and PWT growth accounting, HDI (GDL) and under-5 mortality (DHS) by
      region, J-PAL evaluations and library papers about the country (listed, not summarized).
- [x] **"What works" review** (`report_recipes.what_works`, Oct 2026): J-PAL evaluations grouped
      by outcome and region with their results quoted (disagreements shown side by side, not
      judged), one OpenAlex search for reviews, 3 library passages; the summary may cite only
      those, checked by code.
- [x] **Poverty profile** (`report_recipes.poverty_profile`, Oct 2026): $3.00/$4.20/$8.30 by survey
      year, poverty gap, Gini, number of poor (computed), survey timing, peers, income per person
      by region (GDL — no free source for poverty rates by region).

## Phase 6: An interface for anyone

- [x] Local web UI (`web.py`, Oct 2026): chat with inline charts and tables, live progress,
      data download as a zip (full series re-read from the source, what the model saw, or
      both; run_python scripts are in the technical view). Localhost only, by design.
- [x] Two answer levels: plain-language by default, a "technical" toggle built from the tool
      results (no tokens) plus an optional one-request technical rewrite; a 43-term glossary.
- [x] Tool trail and fact-check status on every answer.
- [x] Others use it by running their own copy (decided Oct 2026, not a public site):
      `setup_assistant.py` + README "Getting started"; missing pieces (papers, Atlas, Ask,
      Ollama, GDL, J-PAL, PWT, non-Mac run_python) switch off and show on the page's Setup panel.

---

## Constraints and decisions

- **8GB RAM:** fine — large datasets live in local files (Parquet/DuckDB), not memory. Only
  the models compete for RAM, unchanged from today.
- **Groq free tier:** every phase works within it, just slowly. More tools and more data per
  answer push toward the paid Dev tier; revisit after Phase 1's benchmark.
- **Keys:** everything above is keyless except FRED (have it). BLS/BEA/Census are no longer
  needed with the development focus.
- **Downloads** (papers, datasets): listed and approved before each phase starts.
- **Existing US work stays**: the Opportunity Atlas and mobility papers remain a module;
  the new focus is additive.
