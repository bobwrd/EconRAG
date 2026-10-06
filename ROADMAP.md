# Roadmap: a development-economics research assistant

**Goal:** something anyone (students, journalists, analysts, researchers) would use for
development-economics questions *instead of* general chatbots like ChatGPT.

**Positioning.** General chatbots write and reason better than a free open model, and that
won't change. This project wins on what they can't do reliably:

| What users need | General LLMs | This project |
|---|---|---|
| Exact, current numbers (poverty, GDP, health, education) | Often outdated or guessed | Fetched live from the source, computed in Python, fact-checked |
| Provenance | Vague | Every number names its source, series, and year |
| Reproducibility | No | Every answer can export its data (CSV) and a script that recreates each number |
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

## Status (Oct 6 2026)

| Phase | State |
|---|---|
| 0. Measure | Done. Baseline 27/30 on `main`; benchmark now 45 questions, re-run on `after-benchmark` pending |
| 1. Core data | Done except Our World in Data series beyond Maddison |
| 2. Computation | Helper library done (`devecon.py`); `run_python` tool and charts not started |
| 3. Literature | Development library (15 papers), OpenAlex, citation checks done; RCT databases not started |
| 4. Long-run / subnational | Maddison and Penn World Table 11.0 live; subnational not started |
| 5. Workflows | Not started |
| 6. Interface | Not started |

All Oct 6 work is on branch `after-benchmark` (uncommitted): benchmark fixes, peer groups,
`devecon.py`, `longrun.py`, the development library, multi-column PDF extraction.

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
      correct answers. All fixed in code on branch `after-benchmark` (not yet re-measured).
      14 harder questions added for the next phases (31 → 45): 5 `compute` (growth rates,
      doubling time, ratios, population-weighted averages, peer groups), 5 `dev_lit`
      (microcredit, deworming, cash transfers, graduation, the $3.00 line), 4 `longrun`.
      Next: `benchmark.py run after-fixes` on the branch (~2 days of Groq budget).
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
      through 2028, 143 economies + regions), labeled FORECAST with the edition date. IMF WEO
      still out: its API rejects Python clients and DBnomics' mirror is stale (Apr 2025). GEP
      has growth only — inflation/debt forecasts remain a gap.
- [~] **Our World in Data** (CSV downloads): used for Maddison GDP per capita (Phase 4);
      other curated series not added yet.
- [x] **Country handling in code**: names, ISO codes, aliases ("Ivory Coast" → CIV,
      "DRC" → COD), typo tolerance, regions and income groups; "Congo" rejected as ambiguous.
      Peer groups: `get_data(peers=true)` adds the income-group and region aggregates plus the
      country's rank/median/percentile among members (computed in Python).
- [x] FRED and the Opportunity Atlas kept as modules (FRED now via the same two tools).

## Phase 2: Computation sandbox

- [ ] `run_python` tool: the model writes pandas/statsmodels code over fetched data
      (cross-country regressions, convergence, growth decompositions). Runs in a subprocess
      with no network, a time limit, and an output-size cap. Fine for personal use; **not**
      safe to expose to other users as-is.
- [x] `devecon.py` helper library (28 tests, no network), built Oct 2026; not yet exposed as a tool: PPP conversion, constant-price rebasing, CAGR,
      poverty gap / squared gap, Gini and Lorenz from distributions, per-capita, income-group
      averages (population-weighted vs simple), latest-available-year logic.
- [ ] Charts saved as PNG with source notes; numbers computed in the sandbox are fed to
      `verify.py` so they count as verified.

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
      2026 on branch `phase3-citations-retrieval`; untested against live Groq.
- [ ] **"What works" evidence**: J-PAL evaluations and the 3ie Development Evidence Portal
      as a searchable source of RCT results (intervention → outcome → effect → country).
- [x] Embedding model upgraded to `bge-small-en-v1.5`: better candidates (reworded MRR
      .148 → .262) but no end-to-end gain after Laya's rerank (14/18 and 5-6/12 either way).
      The reranker was then the bottleneck: fusion constant 60 -> 10 lifted evidence in the
      top 5 after reranking 14 -> 16/18 and 5 -> 6/12 (NOTES.md "Reranking").

## Phase 4: Subnational and long-run data

- [ ] **Global Data Lab**: subnational HDI, income, education, and health for ~160
      countries; the development analogue of the county Atlas (`region_profile`,
      `rank_regions`).
- [ ] **DHS Program indicators API**: health, fertility, nutrition, and education from
      household surveys.
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
- [ ] Later, if useful: WHO GHO (health), UNESCO UIS (education), FAOSTAT (agriculture),
      ILOSTAT (labor), OECD CRS (aid flows).

## Phase 5: Workflows people repeat

Multi-step templates built on the same tools, exportable as Markdown/LaTeX with BibTeX plus
data and scripts:
- [ ] **Country brief**: growth, poverty, inequality, human development vs peers + recent research.
- [ ] **"What works" review**: evidence on an intervention (cash transfers, deworming,
      microfinance) by outcome and region, from the RCT databases.
- [ ] **Poverty profile**: levels, trends, survey vintages, subnational spread.
- [ ] **Compare countries** on any set of indicators, with the conventions applied.

## Phase 6: An interface for anyone

- [ ] Local web UI: chat with inline charts and tables, one-click CSV/script download.
- [ ] Two answer levels: plain-language by default, a "technical" toggle (definitions,
      methods, caveats); a glossary for terms like PPP, poverty gap, HDI.
- [ ] Show the tool trail (what was looked up) and the fact-check status on every answer.

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
