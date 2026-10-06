# Local Economics RAG Assistant

An economics research assistant specialized in **development economics** (worldwide) and
**economic opportunity in the US**. Online (with `GROQ_API_KEY`), a tool-using analyst
(`analyst.py`) answers from the sources it chooses between: World Bank World Development
Indicators (~1,500 indicators x 217 economies), any FRED series, Opportunity Atlas county
data (every US county), and a personal library of econ PDFs (mostly Opportunity Insights
papers) — Python computes every number, and `verify.py` checks the answer against
the tool results. Offline (or if Groq fails), the original local pipeline answers with phi3.5.
No paid API: FRED and Groq's free tier are the only network calls.

**Direction (Oct 2026):** refocusing on development economics for a general audience —
see `ROADMAP.md` for the phased plan and what's done (Phases 0a/0b, 1, much of 3). First
benchmark run (Oct 6, main): 27/30 — failures fixed in code on branch `after-benchmark`, which
also adds peer groups, `devecon.py`, `longrun.py` (Phase 4; data files still to be saved by
hand) and 14 harder benchmark questions. Next: re-run the benchmark on that branch. A general chatbot's answers to the same benchmark are
kept locally in `eval/external/` (2/25 strict, ~6/25 lenient; mostly stale numbers and the
old $2.15 poverty line).

## Hardware constraint (read this first)

MacBook Air M2, **8GB RAM**. This is the binding constraint on every design decision below.
Running two memory/GPU-heavy processes at once is what causes the failures documented in
"Known gotchas." When changing this pipeline, always ask: does this risk two things being
resident in memory/GPU at the same time?

## Architecture

```
docs/*.pdf --ingest.py--> data/chunks.json + data/embeddings_<model>.npy   (once per new PDF)

question --route_query()--> live_data | documents | both           (Laya, CPU)
              |                              |
         fred.py (FRED API)         hybrid bge-small + BM25 (top 15)
              |                     --> Laya rerank, fused c=10 (CPU) --> top 5
              |                              |
              +------------> ask_llm() --> Ollama (phi3.5) --> answer
```
That is the **offline / fallback** path. With a Groq key, each question goes first to:
```
question --> analyst.py: gpt-oss-120b (Groq) loop, up to 6 rounds, calling tools:
               search_papers      (same hybrid retrieval + Laya rerank as above)
               county_profile / rank_counties / correlate_counties   (atlas.py)
               search_data / get_data   source=worldbank (worldbank.py, default) or fred (fred.py)
          --> verify.py: numbers + citations must trace to tool results
              (one revision round if not; leftovers flagged "⚠ Not verified")
          --> answer.  Any GroqUnavailable (offline, bad key, retired model, long
              rate limit) --> the local pipeline above for that question.
```

- **Retrieval**: hybrid — `sentence-transformers` (`BAAI/bge-small-en-v1.5`, since Oct 2026;
  was `all-MiniLM-L6-v2`) cosine similarity over a flat NumPy array (no vector DB,
  deliberately) fused by reciprocal rank with BM25 keyword scores (`bm25.py`, built at load,
  no dependency). bge queries take a prefix (`QUERY_PREFIXES`, applied by `load_embedder`).
  Measured on `eval/retrieval_eval.py` (18 original questions) and `--reworded` (12
  paraphrases in everyday words, `eval/reworded_questions.json`), evidence chunk rank:

  | | original: top-5 / top-15 / MRR | reworded: top-5 / top-15 / MRR |
  |---|---|---|
  | BM25 only | 11 / 17 / .528 | 3 / 6 / .179 |
  | MiniLM dense only | 9 / 14 / .395 | 3 / 7 / .111 |
  | bge-small dense only | 9 / 15 / .413 | 3 / 6 / .198 |
  | MiniLM + BM25 (old) | 11 / 17 / .500 | 4 / 6 / .148 |
  | **bge-small + BM25 (now)** | **12 / 17 / .523** | 3 / **7 / .262** |

  Better candidates, but **no end-to-end gain**: after Laya's top-5 (what reaches the
  generator) original 14/18 for both models, reworded MiniLM 6/12 vs bge 5/12 (noise). Laya
  dropped evidence retrieval ranked 3rd/8th, so the reranker was the bottleneck — fixed by
  the fusion constant (see Reranking: now 16/18 and 6/12). Kept bge-small (better pool,
  negligible cost). Weighting dense 2x helped neither set. Neither
  embedding model is strong alone — paraphrase stays hard. Online it matters less: the
  analyst writes its own search queries in the field's vocabulary. Embeddings live in one file per model,
  `data/embeddings_<model>.npy` (`ask.embeddings_path`), so the Phase 1 commit (which reads
  the old single `data/embeddings.npy`, MiniLM) and current code both work from one `data/`;
  `--model <name>` evaluates any model (computed once).
- **Reranking**: Laya (`convaiinnovations/laya`, local, ~421M params) scores each candidate
  chunk with a `score` question (0-3 ordinal relevance) against `"Query: ... \n\nPassage: ..."`.
  Runs via `predict_batch` (one shared forward pass, not N sequential calls).
  Laya's ranking is *fused* with the retrieval ranking (reciprocal rank), not used alone:
  Laya only reads a chunk's first ~470 of its tokens (~44%) and was dropping chunks BM25
  ranked 3rd. Fact reached the prompt for 18/26 eval questions fused vs 16/26 Laya-only.
  **Fusion constant `RERANK_RRF_C` = 10** (Oct 2026; was the textbook 60, at which ranks 1 and
  15 score nearly alike, so neither ranking could promote its best picks). Chosen offline:
  Laya scores for each question's top-30 candidates computed once, ~20 fusion rules compared
  (evidence in top 3/4/5 after reranking; ~3-4 chunks actually fit the prompt):

  | rule | original (18) | reworded (12) |
  |---|---|---|
  | no reranker | 10/11/12 | 3/3/3 |
  | c=60 (old) | 11/14/14 | 4/5/5 |
  | **c=10 (now)**, same for c=5 | **13/14/16** | **5/6/6** |
  | c=20 | 11/14/14 | 5/6/6 |
  | c=10 + Laya also scores the best-matching window, max of both | 13/16/16 | 5/6/6 |
  | Laya only | 10/10/11 | 6/6/7 |
  | bigger pools (20/30), retrieval weight 2, protect retrieval top-2 | no better than c=10 | |

  Not adopted: the window+start variant (+2 at top-4, original set only) doubles Laya time.
  Some evidence never reaches the top 30 (`race_gap_shrink` reworded) — no reranker fixes
  that. 20 rules on 30 questions risks overfitting; c=5/10/20 trending smoothly is the main
  reassurance.
- **Routing**: Laya answers two independent `noul` (yes/no) questions — `needs_live_data`,
  `needs_documents` — rather than one 3-way `choice`. The 3-way version was tried first and
  failed: `both` acts as a statistical attractor bucket in forced 3-way classification and
  dominated almost every query. But only `needs_live_data` turned out to discriminate:
  research questions score <=0.20, live lookups >=0.60. `needs_documents` is weak (usually
  <0.5 even for pure research questions), so the old "both <0.5 -> default to `both`" rule
  sent ~85% of research questions down `both`, wasting a whole extra generation on an
  irrelevant live-data answer. `route_query` now: fetch live if live >= 0.25 or the question
  names a FRED indicator next to a recency word (`ASKS_CURRENT_INDICATOR` — Laya underrates
  the live half of compound questions); search documents unless live >= 0.5 and docs < 0.25.
  Ambiguous cases still go to `both`. 23/23 on the eval set, 15/15 on held-out questions
  (was 7/23, 7/15) — thresholds were tuned on the eval set, so watch for misroutes.
- **Online analyst** (`analyst.py`): Groq `openai/gpt-oss-120b` with OpenAI-style tool
  calling. Design rule: **the model chooses, Python computes** — tools return computed stats
  (FRED max/min with dates, Atlas weighted averages/percentiles/correlations, with the
  correlation's direction spelled out in words), never raw data for the model to summarize.
  The system prompt carries "economics conventions" (employment rate = EMRATIO, not 100 −
  unemployment; CPI via `pc1`; don't present racial composition as a cause). Racial shares
  are excluded from `correlate_counties` for the same reason. Keeps the last 2 Q/A pairs for
  follow-ups; local fallback answers are added to that memory too.
- **Local generation** (offline fallback, and the only path without a key): Ollama `phi3.5`
  (see "Model choice" below). `both`-route questions
  get **two separate single-purpose generations** (one FRED-only, one docs-only), not one
  combined generation — see "Prompting gotchas." Answers stream to the terminal.
- **Context packing**: reranked chunks are packed best-first into what's left of `num_ctx`
  after the instructions, question, and `ANSWER_RESERVE` (768), measured exactly with
  phi3.5's own HF tokenizer (`microsoft/Phi-3.5-mini-instruct`, tokenizer only — verified
  token-for-token against Ollama). Usually ~4 chunks fit; the last is cut at a token
  boundary. See gotcha #5.
- **Not built**: Jev (TypeSafe) A/B comparison — the user has an API key in `.env` but chose
  not to spend the $5 credit on it. Would slot in as an alternate reranker in `ask.py`,
  parallel to `rerank_with_laya`.

## Files

- `ingest.py` — rebuild the index. Run whenever a PDF is added to `docs/`. Always does a
  **full rebuild** (re-embeds every PDF), not incremental — fine at this corpus size (26
  papers, 2,212 chunks, ~95s since the Oct 2026 development library; PDF extraction is the
  cost, run 4 PDFs in parallel processes), not a decision that scales to hundreds of docs.
- `papers.json` — author-year + title for every PDF in `docs/` (docs/ is gitignored); passages
  are labeled with it, so the analyst cites "Banerjee, Karlan and Zinman (2015)" and verify.py
  checks that against the label. A test fails if a PDF in docs/ has no entry.
- `ask.py` — interactive query loop (`.venv/bin/python ask.py`). Reads questions with
  `input()`; also works with piped stdin (exits cleanly on EOF), which is how it's benchmarked.
- `fred.py` — FRED client: 4 series fetched concurrently, retried on 429/5xx, cached 15 min
  in-process (local path). `search_series` / `series_stats` serve any series to the analyst
  (stats + text sparkline). Also `python fred.py` to sanity-check the API key/network.
- `analyst.py` — the online tool-using agent (tool schemas, system prompt, loop).
- `atlas.py` — Opportunity Atlas county data: profile, rank, correlate. `python atlas.py Cook IL`.
- `worldbank.py` — World Bank WDI (+ growth forecasts from Global Economic Prospects, API
  source 27, indicator `NYGDPMKTPKDZ`, labeled FORECAST with the edition date; IMF WEO not
  used — API rejects Python clients and the DBnomics mirror stops at Apr 2025): keyword
  search over the indicator catalog (BM25 + `CORE`
  boost + everyday-word `SYNONYMS`), country-name resolution (aliases, fuzzy), per-country
  stats or all-economy rankings, and convention `NOTES` attached to results (survey years
  for poverty, PPP vs market rates, current vs constant prices). Catalog cached in
  `data/worldbank/` (delete to refresh). `python worldbank.py "extreme poverty" Ethiopia`.
- `longrun.py` — Maddison Project 2023 + Penn World Table 11.0, local files in `data/longrun/`.
  The official xlsx files sit behind dataverse.nl's bot check (times out for scripts and, Oct
  2026, browsers too), so: Maddison from Our World in Data's CSV (`--import`; GDP per capita
  only) and PWT from FRED's copy of the 11.0 release (`--import-fred-pwt`: 1,902 series, 167
  countries, ~30 min once; FRED's country code is ISO2 + "A", e.g. KRA = Korea). The official
  xlsx files, if ever saved there, still work via `--import`. The analyst's `source="longrun"`:
  per-country stats, 2+ countries add ratios/overtaking/divergence year, series
  `pwt.growth_accounting` splits growth per worker into capital, schooling and TFP.
- `devecon.py` — tested development-economics formulas (CAGR, doubling time, rebasing, FGT poverty
  measures, Gini/Lorenz/Palma, population-weighted group means with coverage, growth
  decomposition, beta/sigma convergence). Not yet a tool; meant for the Phase 2 sandbox.
- `PAPER_PROPOSAL.md` — 15 open-access development papers proposed for `docs/`, awaiting approval.
- `dhs.py` — DHS Program API (open, no key): household-survey indicators for ~90 countries,
  `source="dhs"`; headline value = the API's `IsPreferred` row; `regions=true` for subnational
  values (trimmed to the top/bottom 6 in analyst.py). DHS country codes are not ISO2 (India =
  IA, Niger = NI); catalog cached in `data/dhs/`. `python dhs.py "under-5 mortality" Kenya --regions`.
- `gdl.py` — Global Data Lab subnational HDI (`source="gdl"`), from a CSV the user saves into
  `data/gdl/` (downloads need a free account). `lgnic` in the file is ln(GNI per capita, 2021
  PPP $) and `pop` is thousands; columns ending f/m are by sex. `python gdl.py shdi Kenya`.
- `jpal.py` — J-PAL's randomized-evaluation summaries (`search_evaluations` tool). `python
  jpal.py --fetch` once (~90 min: the site answers in ~3s a page, plus a 1s pause; resumable);
  URLs come from the sitemap because the /evaluations listing pages never run out.
- `bm25.py` — keyword scoring shared by paper retrieval and the WDI catalog search.
- `openalex.py` — OpenAlex: `search` (the analyst's `search_literature` tool: real papers with
  abstracts, top 15 by relevance re-ranked with citation counts, versions merged) and
  `check_citation` (does "Author and Author (Year)" match any real paper, +-1 year?). Used by
  the analyst's fact-check to label unsupported citations "real but not retrieved" vs "likely
  invented". Weak for common surnames with "et al." (almost always "exists"); strong for
  invented author combinations. Anonymous budget $0.10/day ≈ 100 searches; no billing on
  file, so never charged (429 when over); optional `OPENALEX_API_KEY` in `.env` = 10x.
- **IMF not integrated (Oct 2026):** its DataMapper API returns every country regardless of
  the requested one (~120KB/call) and answers `curl` but rejects Python `requests` (403/HTML);
  not worked around (would mean disguising the client). Growth forecasts come from the World
  Bank's Global Economic Prospects instead; inflation/debt forecasts remain a gap.
- `verify.py` — fact-checks analyst answers (numbers + citations vs tool results).
- `groq_client.py` — Groq API call + `GroqUnavailable`; `GROQ_MODEL` lives here.
- `tests/test_tools.py` — tool + analyst-loop tests (fake Groq, zero tokens, ~3s). Run after
  every change: `.venv/bin/python tests/test_tools.py`.
- `eval/benchmark.py` — 31-question benchmark of the analyst (dev data, US tools, traps,
  literature), truths computed live from World Bank/FRED/Atlas, scored by code. `run` is
  resumable, records tokens per question, and stops cleanly at Groq's daily cap — a full run
  spans ~2 days of the free budget (rerun the same command to continue); `score`; `questions` /
  `score-external <name>` for pasted answers from general chatbots in `eval/external/` (kept out of git).
- `data/atlas/` — downloaded once (public): `county_outcomes_simple.csv` and
  `cty_covariates.csv` (opportunityinsights.org/data, codebooks = "Table 2" and "Table 10"),
  `national_county.txt` (census.gov FIPS -> county name; the Atlas files have codes only).
- `.env` — `FRED_API_KEY`, `GROQ_API_KEY` (optional; without it everything runs locally),
  `TYPESAFE_API_KEY` (unused so far). Gitignored.
- `data/chunks.json`, `data/embeddings_<model>.npy` — generated, don't hand-edit, regenerate
  via `ingest.py`. `data/embeddings.npy` (MiniLM) is only for checking out the `phase1` tag.
- `eval/reworded_questions.json` — 12 paraphrases of eval questions in everyday words, for
  `retrieval_eval.py --reworded` (where keyword search can't help).
- `eval/questions.json` — 23 questions with expected facts, each checked against the paper
  text (19 docs incl. one deliberately unanswerable, 3 live, 1 both), plus an `evidence`
  phrase per doc question for `eval/retrieval_eval.py` (retrieval-only, seconds per run,
  `--rerank` adds Laya: ~3 min). Caveat: questions were written after reading the abstracts,
  so they share wording with the source — this flatters keyword search; check reworded
  questions before trusting a retrieval change. `eval/run_eval.py
  phi3.5@4096 phi4-mini@8192 ...` compares generation models on identical retrieved context
  and scores routing; answers land in `eval/results/` — read them, the scoring is literal.
  (Before the extraction fix, `covid_wage_split` and `microforecast` failed for every model
  because the fact never reached the context.)

## Environment

- Shell is **fish**, not bash — use `.venv/bin/python`/`.venv/bin/pip` directly rather than
  `source .venv/bin/activate` (that's the bash script; fish needs `activate.fish`).
- Python: `/usr/local/bin/python3.13` (universal arm64/x86_64 binary). The machine also has
  Python 3.10/3.12 installs and a separate global (non-venv) 3.13 site-packages with unrelated
  project junk (streamlit, etc.) — the venv here is intentionally isolated from that.
- **Rosetta gotcha hit once already**: `pip install` under Rosetta translation grabs x86_64
  wheels even on an arm64 Python, and MLX/native ML libs then fail with "incompatible
  architecture." Fix: confirm `arch` prints `arm64`, then
  `pip install --force-reinstall --no-cache-dir <pkg>`.
- Laya's real dependency is **PyTorch (MPS backend)**, not MLX, despite the project's original
  framing — `mlx`/`mlx-lm` are installed globally on this machine but the venv here doesn't
  use them, deliberately, to keep dependencies minimal.

## Known gotchas (hit during development — don't re-discover these)

1. **Laya must load with `device="cpu"`**, not the MPS/GPU default. Running Laya on GPU while
   Ollama is also using the GPU for generation caused a real crash
   (`kIOGPUCommandBufferCallbackErrorOutOfMemory`) that **silently produced NaN scores**
   instead of raising — `rerank_with_laya` now detects NaN and falls back to cosine order
   with a printed warning, but the real fix is avoiding the GPU contention in the first place.
2. **Always pass `num_ctx` explicitly** in Ollama's `options`. Without it, Ollama defaults to
   the model's full native context window (65,536 for phi3.5) and the resulting KV-cache
   allocation thrashes an 8GB machine badly enough that requests silently hang for minutes
   with no error. Current setting: `num_ctx: 4096` (plenty for ~5 chunks + question).
3. **CPI from FRED is a raw index level, not an inflation rate.** Must request
   `units=pc1` (year-over-year % change) or you'll hand the LLM a meaningless number like
   `334.1` that it may confidently mislabel as "the inflation rate."
4. **PDF extraction: don't use pdfplumber's table detection or default spacing.**
   History: `pypdf` flattened tables into runs of numbers (LLM misattributed credit-score
   figures across race groups); the first fix, `find_tables` with the "text" strategy plus a
   words-per-cell filter, turned out to treat most *prose* as tables — 584 of 654 detected
   "tables" were <20% numeric — chopping words into cells ("V-sh | aped") and making whole
   abstracts unreadable (2 of 18 eval facts weren't findable anywhere). Separately, the
   default `x_tolerance=3` glued words together in tightly set papers (6-8% of "words" in the
   Opportunity Insights papers were run-ons like "Weshowthatintergenerational...").
   Now: `x_tolerance=1.5`, no table detection; every page is laid out line by line from word
   positions with " | " only at gaps wider than ~1 character height (`page_lines`). Prose
   comes out clean, tables come out as "Label | 45.4 | 45.1 | -0.7" rows; chunks split on
   line boundaries so rows survive. Glued words now <0.2% everywhere; all 18 facts readable.
   Rotated (non-upright) chars are dropped (figure axis labels extracted reversed).
   Multi-column pages (Oct 2026, for the development library: Science, PNAS, reports with
   two-page spreads): `region_lines` finds a vertical gutter (a gap wider than ~1 character
   height, in the middle 40% of the region) and reads left then right, recursively, so three
   columns work; rows whose text runs across the gutter stay whole. A row counts as evidence of
   columns only if every run of words on it is 3+ words (prose), so tables aren't split —
   verified by the 11 original papers extracting **byte-identical** to before. Columns joined by
   " | ": graduation paper 51% -> 16% of prose lines, deworming 51% -> 0.7%, Smart Buys 50% ->
   14%. Not fixed: the ODI review's chapter-navigation sidebar is glued onto ~1/3 of its lines;
   `dedupe_chars` (for fake-bold doubled letters, "CCOOSSTT") was rejected because it also
   merged real double letters ("Dallas" -> "Dalas"). One NBER PDF lacks a page MediaBox:
   `open_pdf` repairs it in memory with pypdf.

5. **The prompt must fit in `num_ctx` INCLUDING room for the answer.** Ollama silently cuts
   the *start* of an over-long prompt (log: `truncating input prompt`) — i.e. the
   instructions and the top-ranked chunk go first — and if the prompt fills the window there's
   no room left to answer, so it "context shifts" mid-answer and rambles (one test: 2,084
   tokens, 84s). Five 500-word chunks average ~4,000 phi3.5 tokens and table-heavy ones run far
   higher (one real prompt was 9,512 tokens, cut to 4,095). Fixed by `build_doc_context`
   (exact token budget) + `num_predict` capped at the remaining room. Don't raise `num_ctx`
   to fit more chunks without reading gotcha #2: phi3.5's KV cache is ~384KB/token
   (1.5GB at 4096). If more context is ever needed, `OLLAMA_KV_CACHE_TYPE=q8_0` on the Ollama
   server halves that (flash attention is already on) — untested here.
6. **phi3.5 + Laya co-resident = Laya in swap.** phi3.5 holds ~3.6GB of unswappable Metal
   memory; the OS pages Laya's 1.7GB of fp32 weights out, so the next routing call took 7-12s
   (vs 0.5s) and reranking 26-33s (vs 8s). Fix: `warm_up()` runs one tiny Laya pass right
   after each answer, paging it back in while the user types. Unloading phi3.5 after every
   answer instead was measured and was *slower* overall: phi3.5 generated 2-4x slower after
   each cold reload.

7. **Groq free tier: 8,000 tokens/minute, 1,000 requests/day — for every model** (checked
   via `x-ratelimit-*` headers, Oct 2026) — **plus 200,000 tokens per rolling 24h for
   gpt-oss-120b, which is NOT in the headers** (only in the 429 message; found when a day of
   testing exhausted it). At ~10-15K tokens per analyst question that's ~15 questions/day. Every tool round re-sends the whole conversation,
   so questions take ~30-120s of rate-limit waits; a single request over 8K fails outright
   (HTTP 413). Hence: terse tool schemas (~1.6K tokens), compact Atlas output, ~1.8K tokens
   of passages per paper search (`AGENT_PAPERS_CTX`) with already-shown chunks skipped,
   `_fit()` trimming old tool results under `REQUEST_TOKEN_LIMIT`, and waits of up to 60s on
   429 (`MAX_RATE_LIMIT_WAIT`) instead of falling back. Groq's paid Dev tier lifts this.
8. **gpt-oss-120b fabricates despite instructions**: in testing it cited real-sounding papers
   no search returned (and cited research without searching at all), computed "employment
   rate" as 100 − unemployment, read r = −0.44 as a positive relationship, and read a
   recession's peak off sampled points. Fixed in code, not prompt: `verify.py`, worded
   correlation directions, exact max/min per requested range. **verify.py's first version was
   itself broken**: accepting any difference/ratio of any two tool numbers let through 100% of
   random 1-decimal numbers (a county profile has ~150 numbers -> ~90K pairs). Derived numbers
   now count only if both operands appear in the answer (6% false-pass when written, 7.0% re-measured Oct 2026; regression test). Oct 2026 benchmark
   fixes for false alarms on *correct* answers: rounded numbers with a scale word ("237.5
   million"), derived numbers within 0.1% (the model rounds ratios), year differences between
   years named in the answer, digits inside names (COVID-19, G20, series ids), and source tags
   like "(World Bank: X, 2024)" no longer read as author-year citations (side effect: authors
   whose surname is a place name aren't checked). It also "explained" Cook
   County's gap with characteristics that were average (33.8% vs 33.4% single parents), barely
   tracked mobility (short commutes, r = 0.12), or were racial composition read as
   "segregation". Now `county_profile` labels each characteristic vs national (`NOTABLE_SD`
   = 0.5 child-weighted SD), gives its r with mobility, computes
   `characteristics_consistent_with_gap` (notable AND |r| >= `RELEVANT_R` 0.3 AND right
   sign), and omits racial shares unless `demographics=true`; the prompt restricts local
   explanations to that list and bans causal wording. Still unchecked: qualitative claims
   and wording — read answers critically.
9. **gpt-oss ignored `tool_choice: "none"`** (called a tool anyway -> HTTP 400), so the final
   round sends no `tools` at all. **Groq retired its Llama models** in Oct 2026 without the
   docs page updating — list a key's models with `GET /openai/v1/models`.

## Performance experiments that didn't pan out (measured — don't retry blindly)

- **Sub-window embeddings** (tried twice — on the old garbled text and again on clean text
  alongside BM25, still no gain; MiniLM truncates at 256 tokens, chunks are ~720, so retrieval
  only "sees" each chunk's first third): embedding overlapping windows and scoring by
  max/mean/mixed window similarity was no better than whole-chunk embedding on a Laya-judged
  8-query eval (1.82-1.94 vs 1.90 mean relevance), with consistent regressions on some
  queries. Same for feeding Laya the best-matching window instead of the chunk start (Laya
  also truncates, at ~470 of its tokens ≈ 44% of a chunk).
- **Laya on CPU in bf16** (`LAYA_CPU_AMP=bf16`): 13x slower (94s vs 7s per rerank).
- **Laya int8 dynamic quantization**: 2.6x slower on this CPU (qnnpack) AND changed rankings
  (top-5 overlap 2-4/5, routing flipped on 2/10). Rejected.
- **Laya `predict_batch(batch_size=3 or 5)`** to cut peak memory: identical scores but 2-3x
  slower under memory pressure. One batch of 15 stays.
- **torch threads 4 vs 8**: 8 (default) slightly faster.
- **Filtering reference-list chunks**: 27 of 755 chunks, only 3% of retrieval candidates —
  not worth the risk of a classifier deleting real content.

## Prompting gotchas

1. **Small instruction-tuned models reflexively refuse "current data" questions** ("I don't
   have access to real-time data") even when the live figure is right there in the prompt —
   a trained disclaimer pattern-matching on phrasing, not an actual check of the context. Had
   to add an explicit, forceful override in `PROMPT_TEMPLATE` telling the model the FRED
   section was just fetched via API and its training cutoff is irrelevant to it.
2. **A single generation asked to ground itself in both live numbers AND document synthesis
   at once was unreliable** — it would drop the FRED figure entirely, or fabricate a
   plausible-sounding but fake citation (a specific table number, a specific paper title that
   doesn't exist in the actual retrieved chunks) to paper over the gap. This wasn't fixable
   with more prompt engineering — the fix was architectural: `both`-route questions get two
   separate, single-purpose generations (`ask_llm(question, fred_context)` and
   `ask_llm(question, doc_context)` independently), each a pattern that's reliable on its own.
3. Low temperature (`0.2`) is set deliberately — small models hedge or invent more at higher
   temperatures; this pipeline wants literal grounding over creativity.

## Model choice

- **Generation**: `phi3.5` (2.2GB). Started with `qwen2.5:latest` (default tag = 7B, 4.7GB) —
  caused severe swap-thrashing (Ollama reported ~6.9GB resident for that one model alone on
  an 8GB machine). Dropped to `qwen2.5:0.5b` for speed, but it was too weak: oscillated
  between confidently inventing wrong numbers and producing content-free hedges even when
  correct context was available. `phi3.5` (Microsoft's small-but-reasons-well family) fixed
  both failure modes. `qwen2.5:latest` and `qwen2.5:0.5b` are still pulled locally but unused.
- **phi4-mini evaluated (Oct 2026), not adopted**: it has grouped-query attention, so it
  uses 3.1GB at num_ctx 4096 / 3.6GB at 8192 (vs phi3.5's 3.8GB at 4096). Eval: phi3.5@4096
  13/23, phi4-mini@4096 15/23, phi4-mini@8192 14/23 — within noise for 23 questions. Both
  invent wrong numbers sometimes and both sometimes claim "the context doesn't contain" a
  figure that is in it. phi4-mini was slower on doc questions (31s vs 24s — longer answers;
  at 8192 the longer prompt costs the time instead) but faster on live ones. More context
  (all 5 chunks at 8192) did not improve accuracy. Switch with `OLLAMA_MODEL` in `ask.py`
  (`MODELS` holds the verified tokenizer + num_ctx for each). **Caveat:** this comparison
  ran on the old extraction/retrieval and was not repeated after the fixes below — with
  cleaner, better-ranked chunks, phi4-mini@8192 fitting all 5 may now matter more (on the
  retrieval eval, packing into phi3.5's 4096 budget drops some top-5 facts). Worth re-running.
- **Reranker/router**: Laya, always — see architecture above.

## Where quality stands (Oct 2026)

Measured on `eval/` (small sets — treat 1-2 question differences as noise):

| Stage | Before fixes | Now |
|---|---|---|
| Router correct (eval set / held-out) | 7/23, 7/15 | 23/23, 15/15 |
| Eval facts readable in extracted text | 16/18 | 18/18 |
| Fact in top-15 candidates (original / reworded questions) | 15/18, — | 17/18, 7/12 |
| Fact in top-5 after reranking (original / reworded) | 13/18, — | 16/18, 6/12 (15/18, 6/12 with the 25-paper library) |
| Development papers, everyday wording (`--dev`), top-5 after reranking | — | 7/11 |
| Online analyst on the benchmark (main, Oct 6) | — | 27/30 (same 25 as the chatbot: 23/25 vs 2/25) |
| phi3.5 answers correct (first 21 eval questions; run cut short) | 12/21 | 15/21 |
| Prompts silently truncated by Ollama | most doc prompts | none |

Remaining weaknesses, roughly in order of size (1 and 3 are the offline phi3.5 path; the
online analyst's weaknesses are gotcha #8 and whatever the benchmark shows):
1. **The ~4B generator** still states wrong numbers or says "the context doesn't contain" a
   figure that's present, in roughly a quarter to a third of doc answers — even when the
   right chunk is in the prompt. Bounded by 8GB RAM; no prompt fix found.
2. **Reworded questions reach the generator poorly** (6/12 in the top 5 after reranking,
   with `RERANK_RRF_C` = 10; see Reranking). Remaining losses are evidence ranked below 15
   or never retrieved at all — retrieval, not fusion, is the limit again.
3. **Prompt budget**: at num_ctx 4096 only ~3-4 chunks fit, so some reranked-in facts are
   cut. phi4-mini@8192 fits all 5 in less memory (see Model choice) — re-test.

## Running it

```bash
# one-time / after adding a PDF to docs/
cd "/Users/piyushjain/Desktop/Projects/Fun/AI"; .venv/bin/python ingest.py

# ask questions (interactive)
cd "/Users/piyushjain/Desktop/Projects/Fun/AI"; .venv/bin/python ask.py

# compare generation models on the eval set (~45 min for 3 configs)
cd "/Users/piyushjain/Desktop/Projects/Fun/AI"; .venv/bin/python eval/run_eval.py phi3.5@4096 phi4-mini@8192

# sanity-check FRED connectivity/API key alone
cd "/Users/piyushjain/Desktop/Projects/Fun/AI"; .venv/bin/python fred.py
```

If Ollama ever seems to hang with no error for a long time, check `ollama ps` — if it shows
an unexpectedly large context size or the model's been "loaded" for a while with no CPU
activity, `ollama stop <model>` and retry (see gotcha #2 above for the usual cause).
