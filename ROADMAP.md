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

## Phase 0: Measure cheaply

Groq's free tier allows 1,000 requests/day, 8,000 tokens/minute, **and 200,000 tokens per
rolling 24 hours** for gpt-oss-120b (that cap isn't in the response headers — it surfaced
when a day of testing used it up). At ~10-15K tokens per analyst question, one benchmark run
(31 questions) spans about two days of budget; `benchmark.py run` stops cleanly at the cap
and resumes where it left off.

- [x] **0a. Tool tests (zero LLM tokens)** — `tests/test_tools.py`, 21 tests, ~3s, run after
      every change. Also tests the analyst loop against a scripted fake Groq. First catch: the
      number fact-check accepted 100% of random 1-decimal numbers (any-pair arithmetic over
      ~150 tool numbers); now only arithmetic on numbers shown in the answer counts (6%).
- [ ] **0b. Mini benchmark: 31 questions, auto-scored by code (no LLM judge)** —
      `eval/benchmark.py` + `eval/benchmark_questions.json`. Baseline running (Oct 2026).
      - ~15 data questions, answers computed directly from source data by a script, so
        they stay correct when the data updates
      - ~5 literature questions with an expected-fact pattern (like `eval/questions.json`)
      - ~10 **trap questions** that general LLMs get wrong, e.g.:
        poverty at $2.15 (2017 PPP) vs the current line (2021 PPP) · GDP per capita at market
        exchange rates vs PPP · GDP vs GNI · "the poverty rate" when the latest survey is
        years old · nominal vs real growth · headcount ratio vs number of poor people ·
        employment rate vs 100 − unemployment
      - ~120-150 Groq requests and ~300-450K tokens: about two days of the free budget.
- [ ] **0c. General-chatbot comparison at zero API cost** (user). Questions in
      `eval/external/questions.md`; paste answers into `eval/external/<chatbot>.md` under the
      same `## id` headings; grade with `benchmark.py score-external <chatbot>`.
- ~~0d. Smoke set~~ — dropped: 0a covers quick checks for free; run the full benchmark at
  the end of each phase.

## Phase 1: Core development data — built Oct 2026 (World Bank); benchmark pending

Three general tools replace today's FRED-only ones:
`search_data(query)` → a local catalog (series descriptions embedded with the existing
retrieval code), `get_data(source, series, countries, years)` → computed stats per country,
`describe(series)` → definition, units, coverage, caveats.

- [x] **World Bank WDI** (API, no key): 1,498 indicators × 217 economies + aggregates,
      via `search_data` / `get_data` (`worldbank.py`). 10/10 core indicators found first by
      search in tests.
- [x] **Poverty & inequality**: WDI carries PIP's series. Current line verified from the API:
      **$3.00/day, 2021 PPP** (`SI.POV.DDAY`); results carry a survey-year caveat.
- [ ] **IMF World Economic Outlook** (DataMapper API): blocked for now — the API ignores
      the country filter and rejects Python clients (see NOTES.md). Forecasts remain a gap;
      revisit (DBnomics mirrors WEO) in a later phase.
- [ ] **Our World in Data** (CSV downloads): curated long-run series with clear sourcing.
- [x] **Country handling in code**: names, ISO codes, aliases ("Ivory Coast" → CIV,
      "DRC" → COD), typo tolerance, regions and income groups; "Congo" rejected as ambiguous.
      Peer-group comparisons still to do.
- [x] FRED and the Opportunity Atlas kept as modules (FRED now via the same two tools).

## Phase 2: Computation sandbox

- [ ] `run_python` tool: the model writes pandas/statsmodels code over fetched data
      (cross-country regressions, convergence, growth decompositions). Runs in a subprocess
      with no network, a time limit, and an output-size cap. Fine for personal use; **not**
      safe to expose to other users as-is.
- [ ] `devecon` helper library, tested: PPP conversion, constant-price rebasing, CAGR,
      poverty gap / squared gap, Gini and Lorenz from distributions, per-capita, income-group
      averages (population-weighted vs simple), latest-available-year logic.
- [ ] Charts saved as PNG with source notes; numbers computed in the sandbox are fed to
      `verify.py` so they count as verified.

## Phase 3: Literature and evidence

- [ ] **Development library**: open-access PDFs (World Bank Policy Research Working Papers,
      NBER development papers, J-PAL and 3ie evidence reviews), ingested with title, authors,
      and year per chunk so citations are real references, not filenames.
- [x] **OpenAlex** (free, ~250M works): `search_literature` tool (abstracts count as
      evidence for the fact-check) and citation existence checks: unsupported citations are
      labeled "real, not retrieved" vs "likely invented" in the revision feedback. Built Oct
      2026 on branch `phase3-citations-retrieval`; untested against live Groq.
- [ ] **"What works" evidence**: J-PAL evaluations and the 3ie Development Evidence Portal
      as a searchable source of RCT results (intervention → outcome → effect → country).
- [x] Embedding model upgraded to `bge-small-en-v1.5`: better candidates (reworded MRR
      .148 → .262) but no end-to-end gain after Laya's rerank (14/18 and 5-6/12 either way).
      The reranker is now the bottleneck — next retrieval item (NOTES.md "Retrieval").

## Phase 4: Subnational and long-run data

- [ ] **Global Data Lab**: subnational HDI, income, education, and health for ~160
      countries; the development analogue of the county Atlas (`region_profile`,
      `rank_regions`).
- [ ] **DHS Program indicators API**: health, fertility, nutrition, and education from
      household surveys.
- [ ] **Penn World Table** and **Maddison Project** (downloads): long-run GDP, productivity,
      and capital for growth questions ("how did South Korea diverge from Ghana?").
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
