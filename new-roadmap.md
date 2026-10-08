# EconRAG Improvement Roadmap

The core research assistant is complete and working (27/28 on the Oct 7 benchmark). This roadmap
covers production hardening, polish, and distribution improvements, organized in phases.

Ground rules that apply to every phase (from NOTES.md): Groq's free tier is scarce (200K tokens a
day, 8K a minute), so nothing may add text to every request and the benchmark is run only by hand;
the machine has 8GB of RAM; the web UI stays on 127.0.0.1; nothing new is downloaded or installed
without asking first.

Three phases remain: packaging (1), sessions (5) and reports (6). The others (testing & CI,
observability, data management, contributor docs, security) were dropped from this plan on Oct 9;
the pieces of them already built stay: `tests/run_fast.py` + GitHub Actions, token use against
Groq's daily cap, `data_manifest.py`, `SECURITY.md`.

**Done Oct 9 2026:** `pyproject.toml` (1.1), JSON and CSV report exports (6.2).

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
- [ ] Check the data files against `data/MANIFEST.json` (`data_manifest.py --check`)
- [ ] Add estimated times next to the sizes of large downloads
- [ ] `--uninstall`: list what would be deleted (`.venv`, `data/`, `docs/`) and ask before each

#### 1.4 Reproducible install guides
- [ ] Troubleshooting section in README (Rosetta/x86 wheels, Ollama hanging; already in NOTES.md)
- [ ] GitHub Releases with version tags (Docker is not useful here: the sandbox needs macOS)

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
- ✅ Exports: PDF, Word, Markdown and LaTeX zips with charts, BibTeX, JSON, CSV tables, the data zip
- ❌ No saved presets

### Tasks

#### 6.1 Reports tab
- [x] Each report's estimated token cost is already shown under its button (found Oct 9)
- [ ] Saved presets (e.g. "East Africa peers") in the browser

#### 6.2 More export formats ✅ (Oct 9)
- [x] JSON (`report.json`: title, summary and its fact-check, sections, sources, and the fact sheet;
  not the raw tool results) and CSV (`tables_csv.zip`: one file per table or list of studies, cells
  as shown in the report). Both on the Reports tab and in `workflows.py`'s output folder (Oct 9)
- [ ] Optional: a Jupyter notebook that rebuilds the charts from the data

#### 6.3 Customization
- [ ] Choose indicators and year range per report (compare already takes extra indicators)
- [ ] Custom peer groups

---

## Implementation Order

### Done ✅ (Oct 9)
1. `pyproject.toml` (1.1)
2. JSON/CSV report export (6.2)

### Next
3. Sessions in SQLite and the session sidebar (5.1-5.2)
4. Small setup and README items (1.2-1.4)
5. Evidence inspector, comparison and re-run (5.3-5.5)
6. Report presets and customization (6.1, 6.3)

---

## Success Criteria

- **Phase 1**: `pip install -e .` gives the four `econrag-*` commands
- **Phase 5**: past answers survive a restart and can be compared
- **Phase 6**: reports also export as JSON and CSV (done); saved presets and custom peer groups

---

## Implementation Notes

- Run only the tests for the code changed; analyst.py prompt or tool changes also run
  `tests/test_tools.py analyst`.
- Never benchmark as part of a phase unless asked: the free Groq budget can't afford it.
- Ask before: installing pytest/ruff or anything else, adding anything that sends extra text with
  every Groq request, or reorganizing files.
