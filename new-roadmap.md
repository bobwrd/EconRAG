# EconRAG Improvement Roadmap

The core research assistant is complete and working (27/28 on the Oct 7 benchmark). This roadmap
covers production hardening, polish, and distribution improvements, organized in phases.

Ground rules that apply to every phase (from NOTES.md): Groq's free tier is scarce (200K tokens a
day, 8K a minute), so nothing may add text to every request and the benchmark is run only by hand;
the machine has 8GB of RAM; the web UI stays on 127.0.0.1; nothing new is downloaded or installed
without asking first.

**Done Oct 9 2026 (quick wins):** `pyproject.toml` (1.1), `tests/run_fast.py` (2.2/2.3, no pytest
needed), a GitHub Actions workflow for the tests that need no data files (2.1, minimal),
`data_manifest.py` (4.1, minimal), `SECURITY.md` (8.1).

---

## Phase 1: Packaging & Installation (High Priority)

### Goal
Make installing a copy reproducible and give the commands proper names.

**Status:** Started
- ✅ `requirements.txt`: every package already pinned to an exact version
- ✅ `setup_assistant.py` already reports what's set up and installs each missing step, asking before every download
- ✅ `pyproject.toml` with metadata, the same pins, a `dev` extra and four commands (Oct 9)
- ❌ No versioned releases

### Tasks

#### 1.1 Create pyproject.toml ✅ (Oct 9)
- [x] Metadata: name `econrag`, version 0.1.0, author, licence, description, `requires-python >=3.11`
  (the same check `setup_assistant.py` makes; only ever tested on 3.13)
- [x] Runtime dependencies = `requirements.txt`, same pins. `laya` is a **runtime** dependency
  (it is the reranker and router), not an optional one
- [x] `dev` extra: pytest, ruff (optional: the tests are plain scripts and don't need pytest)
- [x] Not pip packages, so not listed: Ollama (an app, for offline answers) and Ask (charts,
  `vendor/ask` with its own `.venv`); `setup_assistant.py` handles both
- [x] Commands: `econrag-web`, `econrag-ask`, `econrag-setup`, `econrag-report` (`workflows.py`)
- [x] Works only as an **editable** install (`pip install -e .`): the modules read `ui/`,
  `papers.json`, `data/` and `docs/` from the folder they sit in
- [ ] Project URLs (repository, issues) once the repository address is final
- [ ] Not yet tried: `pip install -e .` itself (it downloads setuptools into a temporary build
  environment, so ask first)

#### 1.2 Dependency files
- [x] `requirements.txt` is already fully pinned
- [ ] Optional: `requirements-dev.txt` if anyone prefers it to `pip install -e ".[dev]"`
- [ ] Document the dependency strategy (pins in both files must change together)

#### 1.3 Enhance setup_assistant.py
`python3 setup_assistant.py` with no arguments is already the "doctor": it checks Python, the
packages, `.env` keys, the data files, the local models, Ollama, Ask and the sandbox.
- [ ] Add a `verify` step that runs `tests/run_fast.py --ci` after installing
- [ ] Check the data files against `data/MANIFEST.json` (see 4.2)
- [ ] Add estimated times next to the sizes of large downloads
- [ ] `--uninstall`: list what would be deleted (`.venv`, `data/`, `docs/`) and ask before each

#### 1.4 Reproducible install guides
- [ ] Troubleshooting section in README (Rosetta/x86 wheels, Ollama hanging; already in NOTES.md)
- [ ] GitHub Releases with version tags (Docker is not useful here: the sandbox needs macOS)

---

## Phase 2: Testing & CI (High Priority)

### Goal
Run the tests automatically on every push, without spending Groq tokens.

**Status:** Started
- ✅ 12 test files in `tests/`, each a plain script with its own PASS/FAIL list (pytest also
  collects them if installed); zero Groq tokens (a scripted fake Groq)
- ✅ `tests/run_fast.py` runs the quick ones in one go (Oct 9)
- ✅ `.github/workflows/tests.yml` (Oct 9) — not yet run on GitHub: it starts on the first push
- ❌ Most tests need `data/` files, which are gitignored, so GitHub's copy can't run them

### Tasks

#### 2.1 GitHub Actions ✅ minimal version (Oct 9)
- [x] On push to main, pull requests and by hand: macOS, Python 3.13, `pip install -r
  requirements.txt`, then `python tests/run_fast.py --ci` (devecon, manifest, setup tests)
- [ ] Watch the first run: installing torch etc. may take several minutes
- [ ] Only macOS makes sense (run_python's sandbox is macOS-only). A Python 3.11/3.12 matrix is
  possible later; there are no macOS 11/12 runners any more
- [ ] More tests in CI need small sample data files committed under `tests/fixtures/` (the real
  files are 43 MB and some can't be redistributed — check licences first)

#### 2.2 Tests by speed and scope
- [x] **Quick, no data files** (`run_fast.py --ci`): `test_devecon`, `test_manifest`, `test_setup`
- [x] **Quick, need data/ and some network** (`run_fast.py`): `test_compute`, `test_tools`,
  `test_web`, `test_reports` (World Bank and FRED are real; Groq is faked)
- [ ] **Per data source** (run after changing that source): `test_dhs`, `test_gdl`, `test_imf`,
  `test_jpal`, `test_longrun`
- **Benchmark**: stays manual. A full run uses about two days of Groq's free budget and needs the
  key, so it can't gate CI or run weekly on the free tier

#### 2.3 Test runner ✅ (Oct 9)
- [x] `tests/run_fast.py` instead of `pytest.ini`: no new install needed, keeps the existing
  test style
- [ ] Optional later: pytest markers, if pytest is ever adopted

#### 2.4 Contract tests for each data provider
- [ ] A saved sample reply per source (World Bank, FRED, IMF, DHS, OpenAlex, J-PAL) and a test
  that the parser still reads it: catches format changes without the network. `test_imf.py`
  already does this for the IMF fallback
- [ ] Error-handling tests (429, 404, malformed JSON) where not covered yet

#### 2.5 Lint
- [ ] `ruff check` (default rules; configured in `pyproject.toml`) once ruff is installed (ask
  first). No black: it would rewrite every file and bury real changes in the history
- [ ] Optional: a pre-commit hook

---

## Phase 3: Observability & Debugging (High Priority)

### Goal
See what happened during a question: which tools ran, how long they took, how many tokens.

**Status:** Partly there
- ✅ The web UI's tool trail shows every tool call; `eval/benchmark.py` records tokens per question
- ❌ No log file
- ❌ No daily token totals

### Tasks

#### 3.1 Logging (medium-sized job, not a quick win)
- [ ] Python's `logging` to `logs/econrag.log` (gitignored), one line per tool call, data
  fetch, Groq request (tokens, latency) and fact-check result
- [ ] `ECONRAG_LOG_LEVEL` environment variable
- [ ] Never log keys (see 8.5)

#### 3.2 Audit trail / provenance
- [ ] Save each answer's tool calls, sources and fact-check result as JSON (the web UI already
  has these in memory for its data zip)

#### 3.3 Token usage tracking
- [ ] Add up the tokens Groq/OpenRouter report for each request into `data/usage/<date>.json`
  (no extra tokens sent: the counts come back with every reply)
- [ ] Show today's total against the 200K daily limit in the web UI

#### 3.4 Debug mode
- [ ] `ECONRAG_DEBUG=1`: save raw API replies to `data/debug/`

#### 3.5 Data source health checks
- [ ] `health_check.py`: one tiny request per source, response time and status. Run by hand;
  could feed the setup panel

---

## Phase 4: Data Management & Versioning (High Priority)

### Goal
Know which version of each dataset an answer used.

**Status:** Started
- ✅ The chat's data zip already has the full series, what the model saw, or both
- ✅ `data_manifest.py` (Oct 9): `data/MANIFEST.json` with source, kind, size, date and SHA-256
  for every data file; `--check` lists missing, new and changed files
- ❌ No answer records which file version it used

### Tasks

#### 4.1 Dataset list ✅ minimal (Oct 9)
- [x] `python data_manifest.py` writes the list; `--check` compares
- [x] "Cache" files (World Bank and DHS catalogs, IMF) are refreshed by the program on its own, so
  their changes are reported separately as expected
- [ ] Update the list automatically after each import (`longrun.py --import`, `jpal.py --fetch`,
  `ingest.py`, setup steps)
- [ ] Record each dataset's version (Maddison 2023, PWT 11.0, GDL v10.2, WEO edition)

#### 4.2 Checksum validation
- [ ] `setup_assistant.py` runs the check and warns about changed files
- [ ] Warn when a "downloaded once" file is older than N days

#### 4.3 Export data with answers
- [ ] Add to the existing data zip: a `provenance.json`, the package versions, the
  run_python scripts (scripts are already shown in the Technical view)

#### 4.4 Reproducibility
- [ ] Save Python version, model name, Groq/OpenRouter request IDs and time with each answer
- [ ] "Re-run": re-fetch the same series and list the numbers that changed

#### 4.5 Paper library
- [ ] `papers.json` already holds author-year, title and URL for each PDF; add a checksum and
  licence / open-access status

---

## Phase 5: User Experience & Sessions (Medium Priority)

### Goal
Keep answers across restarts and compare them.

**Status:** Partly there
- ✅ Each answer shows the tool trail, fact-check badge, charts, a Plain/Technical toggle and data download
- ✅ Answers are kept in memory (last 50) while web.py runs
- ❌ Lost when web.py stops
- ❌ No comparison between answers

### Tasks

#### 5.1 Session storage
- [ ] SQLite (built into Python, nothing to install): sessions, questions, answers, tool results
- [ ] "Recent sessions" list on start

#### 5.2 Session UI
- [ ] Sidebar of past sessions; reopen one
- [ ] "Export session" (zip)

#### 5.3 Evidence inspector
- [ ] Click a number in the answer → the tool result it came from (verify.py already matches
  each number to a tool result)

#### 5.4 Version comparison
- [ ] Side by side: two answers to the same question, with the numbers that differ

#### 5.5 Re-run
- [ ] Re-fetch the data for an old answer and show what changed (no Groq tokens unless the user
  asks for a new summary)

---

## Phase 6: Report Templates & Export (Medium Priority)

### Goal
Build on the existing reports.

**Status:** Mostly done
- ✅ Four reports (`workflows.py`, `report_recipes.py`): compare countries, country brief,
  poverty profile, what works
- ✅ The web UI's Reports tab, with a "Write a summary" checkbox (unticked = free)
- ✅ Exports: PDF, Word, Markdown and LaTeX zips with charts, BibTeX, the data zip
- ❌ No JSON or CSV export; no saved presets

### Tasks

#### 6.1 Reports tab
- [ ] Show each report's estimated token cost next to the button (the README already lists them)
- [ ] Saved presets (e.g. "East Africa peers") in the browser

#### 6.2 More export formats
- [ ] JSON (the report dict as it is) and CSV (one file per table): small additions to `report_export.py`
- [ ] Optional: a Jupyter notebook that rebuilds the charts from the data

#### 6.3 Customization
- [ ] Choose indicators and year range per report (compare already takes extra indicators)
- [ ] Custom peer groups

---

## Phase 7: Code Quality & Contributor Experience (Low Priority)

### Goal
Make it easy for someone else to understand and extend the code.

**Status:** Partly there
- ✅ Detailed comments; NOTES.md explains every design decision and gotcha
- ❌ No contributor guide or architecture page

### Tasks

#### 7.1 CONTRIBUTOR_GUIDE.md
- [ ] Setting up a copy, running the tests (`tests/run_fast.py`)
- [ ] Adding a data source: the pattern of `imf.py`/`dhs.py` (a module, a `source=` value in
  `search_data`/`get_data`, tests, an entry in `compute.SOURCES`) — and the token cost of
  changing tool schemas
- [ ] Adding a benchmark question (`eval/benchmark.py`)

#### 7.2 Code organization
- [ ] **Keep the flat layout** and document it in ARCHITECTURE.md. Moving everything into
  `src/econrag/` would touch almost every import and file path for little gain

#### 7.3 Architecture documentation
- [ ] ARCHITECTURE.md: the diagram from NOTES.md, the question → tools → model → fact-check
  flow, which file does what
- [ ] `docs/` is gitignored (it holds the PDFs), so registries of data sources and tools go in
  the repo root or a new folder

#### 7.4 Docstrings
- [ ] Docstrings for public functions that lack them

---

## Phase 8: Security & Deployment (Low Priority)

### Goal
Write down the security model; stay localhost-only.

**Status:** Started
- ✅ Web UI on 127.0.0.1 only, Host and Origin checks
- ✅ run_python sandbox: no network, writes only to its temp folder, 10 s CPU, 15 s in all, 3,000 characters of output
- ✅ `SECURITY.md` (Oct 9)
- ℹ️ Design: one person, their own copy

### Tasks

#### 8.1 Security model ✅ (Oct 9)
- [x] `SECURITY.md`: what is trusted, what protects you, known limits, what a server would need

#### 8.2 Keys
- [ ] Make sure keys never appear in logs or error messages (with 3.1)

#### 8.3 run_python sandbox
- [ ] Limits are documented in SECURITY.md. A container is the route if this ever leaves one Mac

#### 8.4 Rate limits (already handled where it matters)
- Groq: per-minute waits and the OpenRouter fallback already exist (NOTES.md gotcha 7)
- OpenAlex: anonymous budget ~100 searches a day (not 100,000)
- FRED: 120 requests a minute

#### 8.5 Sanitization
- [ ] Never log keys; redact them from error messages shown in the UI

---

## Implementation Order

### Quick wins ✅ (done Oct 9)
1. `pyproject.toml` (1.1)
2. `tests/run_fast.py` + GitHub Actions for the no-data tests (2.1-2.3)
3. `data_manifest.py` (4.1)
4. `SECURITY.md` (8.1)

### Next (small to medium, no Groq tokens)
5. Watch the first GitHub Actions run; fix what it finds
6. Token usage totals (3.3) — reads counts Groq already returns
7. JSON/CSV report export (6.2)
8. Manifest check in `setup_assistant.py` (4.2)

### Then
9. Logging (3.1) and provenance per answer (3.2, 4.3)
10. Sessions in SQLite (5.1-5.2)
11. Contract tests with saved replies (2.4)
12. ARCHITECTURE.md and CONTRIBUTOR_GUIDE.md (7.1, 7.3)

---

## Success Criteria

- **Phase 1**: `pip install -e .` gives the four `econrag-*` commands
- **Phase 2**: every push runs the no-data tests on GitHub; the benchmark is re-run by hand after big changes
- **Phase 3**: a log file shows each question's tool calls and token use
- **Phase 4**: `data_manifest.py --check` passes, and each answer records its data versions
- **Phase 5**: past answers survive a restart and can be compared
- **Phase 6**: reports also export as JSON and CSV
- **Phase 7**: a newcomer can add a data source by following the guide
- **Phase 8**: security model documented; no secrets in logs

---

## Implementation Notes

- Run only the tests for the code changed; analyst.py prompt or tool changes also run
  `tests/test_tools.py analyst`.
- Never benchmark as part of a phase unless asked: the free Groq budget can't afford it.
- Ask before: installing pytest/ruff or anything else, adding anything that sends extra text with
  every Groq request, or reorganizing files.
- Skip Phase 8 beyond documentation unless the plan changes to public deployment.
