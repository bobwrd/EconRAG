"""
Sets up your own copy of the assistant, one step at a time.

    python3 setup_assistant.py              # check what's set up (changes nothing)
    python3 setup_assistant.py install      # walk through every missing step, asking first
    python3 setup_assistant.py install papers   # run one step (names below)

Every download is announced with its source and size, and needs your yes.
Only the packages and a Groq key are needed to start; everything else adds a
data source or feature, and the assistant switches off whatever is missing.
Python's standard library only: it runs before anything is installed.
"""

import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
WINDOWS = os.name == "nt"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
ASK_DIR = ROOT / "vendor" / "ask"
ASK_PY = ASK_DIR / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
DATA = ROOT / "data"
DOCS = ROOT / "docs"
ENV = ROOT / ".env"
PACKAGES = ["laya", "sentence_transformers", "transformers", "torch", "pdfplumber", "dotenv", "requests", "scipy",
            "fpdf", "docx"]
ATLAS_FILES = {
    "county_outcomes_simple.csv": ("https://opportunityinsights.org/wp-content/uploads/2018/10/county_outcomes_simple.csv", "1.7 MB"),
    "cty_covariates.csv": ("https://opportunityinsights.org/wp-content/uploads/2018/12/cty_covariates.csv", "1.1 MB"),
    "national_county.txt": ("https://www2.census.gov/geo/docs/reference/codes/files/national_county.txt", "0.1 MB"),
}
MADDISON_URL = ("https://ourworldindata.org/grapher/gdp-per-capita-maddison-project-database.csv"
                "?v=1&csvType=full&useColumnShortNames=true")
HF_MODELS = ["convaiinnovations/laya", "BAAI/bge-small-en-v1.5", "microsoft/Phi-3.5-mini-instruct"]
KEYS = [  # (name, needed?, where to get it, what it's for)
    ("GROQ_API_KEY", True, "https://console.groq.com/keys",
     "the online analyst (free tier: about 15 questions a day)"),
    ("FRED_API_KEY", False, "https://fred.stlouisfed.org/docs/api/api_key.html",
     "US data from FRED, and the Penn World Table import"),
    ("OPENROUTER_API_KEY", False, "https://openrouter.ai/keys",
     "a free backup model when Groq's daily limit runs out"),
]


# ------------------------------------------------------------------ helpers
def say(text: str = ""):
    print(text, flush=True)


def ask_yes(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run(cmd: list, cwd: Path = ROOT) -> bool:
    say("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], cwd=cwd).returncode == 0


def download(url: str, dest: Path, expect_pdf: bool = False) -> bool:
    """Saves url to dest (via a .part file, so an interrupted download never looks finished)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (setup_assistant.py)"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(part, "wb") as out:
            shutil.copyfileobj(response, out)
    except (urllib.error.URLError, OSError) as e:
        say(f"  ✗ {dest.name}: {e}")
        part.unlink(missing_ok=True)
        return False
    if expect_pdf and not part.read_bytes()[:5].startswith(b"%PDF"):
        say(f"  ✗ {dest.name}: the site sent a web page, not a PDF (it may need a browser download)")
        part.unlink()
        return False
    part.replace(dest)
    say(f"  ✓ {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return True


def env_keys() -> dict[str, str]:
    keys = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                keys[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return {k: v for k, v in keys.items() if v}


def papers() -> dict:
    return json.loads((ROOT / "papers.json").read_text())


def hf_cached(repo: str) -> bool:
    home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return (home / "hub" / ("models--" + repo.replace("/", "--"))).exists()


def ollama_has(model: str) -> bool | None:
    """True/False whether Ollama has the model; None when Ollama isn't installed."""
    if not shutil.which("ollama"):
        return None
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.split(":")[0] == model for line in out.splitlines()[1:])


# ------------------------------------------------------------------ steps
# Each step: check() -> (ok, detail); install() does it (after asking); `needed`
# steps are required to start, the rest add features.

def check_python():
    ok = sys.version_info >= (3, 11)
    return ok, f"Python {platform.python_version()}" + ("" if ok else " — needs 3.11 or newer (3.13 recommended)")


def check_packages():
    if not VENV_PY.exists():
        return False, "no .venv yet"
    code = f"import importlib.util as u, sys; sys.exit(sum(u.find_spec(m) is None for m in {PACKAGES!r}))"
    ok = subprocess.run([str(VENV_PY), "-c", code]).returncode == 0
    return ok, ".venv with all packages" if ok else ".venv exists but packages are missing"


def install_packages():
    say("Creates .venv and installs requirements.txt from PyPI (PyTorch, sentence-transformers, ...).\n"
        "  About 1.1 GB on disk, a few minutes.")
    if not ask_yes("Install?"):
        return
    if not VENV_PY.exists() and not run([sys.executable, "-m", "venv", ".venv"]):
        return
    run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip"])
    run([VENV_PY, "-m", "pip", "install", "-r", "requirements.txt"])


def check_keys():
    keys = env_keys()
    missing = [k for k, *_ in KEYS if k not in keys]
    if "GROQ_API_KEY" not in keys:
        return False, "no GROQ_API_KEY in .env (without it, only the offline model answers)"
    return True, "GROQ_API_KEY set" + (f"; not set (optional): {', '.join(missing)}" if missing else "")


def _key_works(name: str, value: str) -> bool | None:
    """Asks the key's own service whether it's valid (free calls: no tokens used)."""
    if name == "GROQ_API_KEY":
        url, headers = "https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {value}"}
    elif name == "FRED_API_KEY":
        url, headers = (f"https://api.stlouisfed.org/fred/series?series_id=GDP&file_type=json&api_key={value}", {})
    else:
        return None
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={**headers, "User-Agent": "Mozilla/5.0"}), timeout=20)
        return True
    except urllib.error.HTTPError:
        return False
    except urllib.error.URLError:
        return None  # offline: can't tell


def install_keys():
    keys = env_keys()
    say("Free API keys, saved in .env in this folder (never committed: .gitignore lists it).\n"
        "  Paste a key and press Enter, or just press Enter to skip it. Typing is hidden.")
    lines = []
    for name, needed, url, purpose in KEYS:
        if name in keys:
            continue
        say(f"\n{name} — {purpose}\n  Get one at {url}" + ("" if needed else " (optional)"))
        value = getpass.getpass(f"  {name}: ").strip()
        if not value:
            continue
        works = _key_works(name, value)
        say("  ✓ the service accepted it" if works else "  ✗ the service rejected it — saved anyway; check it"
            if works is False else "  (couldn't check it now)")
        lines.append(f"{name}={value}")
    if lines:
        with open(ENV, "a") as f:
            f.write(("\n" if ENV.exists() and ENV.read_text() and not ENV.read_text().endswith("\n") else "")
                    + "\n".join(lines) + "\n")
        if not WINDOWS:
            ENV.chmod(0o600)  # readable by you only
        say(f"  Saved {len(lines)} key(s) to .env")


def check_atlas():
    missing = [f for f in ATLAS_FILES if not (DATA / "atlas" / f).exists()]
    return not missing, "all 3 files" if not missing else f"missing {', '.join(missing)}"


def install_atlas():
    say("Opportunity Atlas county data (opportunityinsights.org) and the Census county list (census.gov):")
    for f, (url, size) in ATLAS_FILES.items():
        say(f"  {f}: {size} from {url.split('/')[2]}")
    if ask_yes("Download (about 3 MB)?"):
        for f, (url, _) in ATLAS_FILES.items():
            if not (DATA / "atlas" / f).exists():
                download(url, DATA / "atlas" / f)


def check_papers():
    listed = papers()
    have = [f for f in listed if (DOCS / f).exists()]
    return len(have) == len(listed), f"{len(have)} of {len(listed)} PDFs in docs/"


def install_papers():
    missing = {f: m for f, m in papers().items() if not (DOCS / f).exists()}
    auto = {f: m for f, m in missing.items() if m.get("url") and not m.get("browser_only")}
    manual = {f: m for f, m in missing.items() if f not in auto}
    if auto:
        hosts = sorted({m["url"].split("/")[2] for m in auto.values()})
        say(f"{len(auto)} open-access papers from their authors' or publishers' sites ({', '.join(hosts)}).\n"
            "  About 115 MB if all are missing. For your own use; they aren't redistributed.")
        if ask_yes("Download?"):
            for f, m in auto.items():
                say(f"  {m['cite']}")
                download(m["url"], DOCS / f, expect_pdf=True)
    for f, m in manual.items():
        say(f"\nNeeds a browser download: {m['cite']}, \"{m['title']}\"\n  Open {m.get('url', '(no link)')}\n"
            f"  and save it as docs/{f}")
    if (DOCS.exists() and any(DOCS.glob("*.pdf"))):
        say("\nThen build the paper index: the 'index' step.")


def check_index():
    chunks = DATA / "chunks.json"
    if not chunks.exists() or not any(DATA.glob("embeddings_*.npy")):
        return False, "not built (run ingest.py)"
    newer = [p.name for p in DOCS.glob("*.pdf") if p.stat().st_mtime > chunks.stat().st_mtime]
    return not newer, "built" if not newer else f"out of date: {len(newer)} PDF(s) added since"


def install_index():
    pdfs = list(DOCS.glob("*.pdf"))
    if not pdfs:
        say("No PDFs in docs/ yet: run the 'papers' step first.")
        return
    say(f"Builds the search index of the {len(pdfs)} PDFs in docs/ (ingest.py, about 2 minutes).\n"
        "  The first run downloads the embedding model BAAI/bge-small-en-v1.5 from Hugging Face (~130 MB).")
    if ask_yes("Build it?"):
        run([VENV_PY, "ingest.py"])


def check_models():
    missing = [m for m in HF_MODELS if not hf_cached(m)]
    return not missing, "downloaded" if not missing else f"not downloaded yet: {', '.join(missing)}"


def install_models():
    say("Downloads the models the assistant runs on this computer, from Hugging Face, and checks they load:\n"
        "  Laya reranker/router (~1.6 GB), bge-small embeddings (~130 MB), phi3.5's tokenizer (~2 MB).\n"
        "  Otherwise this happens the first time you start the assistant. Uses ~2.5 GB of memory briefly.")
    if ask_yes("Download?"):
        run([VENV_PY, "-c", "from concurrent.futures import ThreadPoolExecutor; import ask; "
                            "ask.load_models(ThreadPoolExecutor(4)); print('Models load fine.')"])


def check_longrun():
    meta = DATA / "longrun" / "meta.json"
    if not meta.exists():
        return False, "not set up"
    return True, "Maddison" + (" + Penn World Table" if (DATA / "longrun" / "pwt.csv").exists() else
                              " only (Penn World Table: the 'pwt' step)")


def install_longrun():
    say("Maddison Project GDP per capita back to year 1, via Our World in Data (~0.6 MB, CC BY 4.0).")
    if ask_yes("Download?") and download(MADDISON_URL, DATA / "longrun" / "owid_maddison_gdppc.csv"):
        run([VENV_PY, "longrun.py", "--import"])


def check_pwt():
    ok = (DATA / "longrun" / "pwt.csv").exists()
    return ok, "imported" if ok else "not imported (optional; output, capital, schooling, productivity)"


def install_pwt():
    if "FRED_API_KEY" not in env_keys():
        say("Needs FRED_API_KEY in .env (the 'keys' step): the table comes from FRED's copy.")
        return
    if not (DATA / "longrun" / "meta.json").exists():
        say("Run the 'longrun' step first.")
        return
    say("Penn World Table 11.0 from FRED's copy: ~1,900 series, about 30 minutes at FRED's rate limit.")
    if ask_yes("Import?"):
        run([VENV_PY, "longrun.py", "--import-fred-pwt"])


def check_jpal():
    ok = (DATA / "jpal" / "evaluations.json").exists()
    return ok, "fetched" if ok else "not fetched (optional; 'what works' evidence)"


def install_jpal():
    say("J-PAL's ~1,300 randomized-evaluation summaries from povertyactionlab.org, one page a second:\n"
        "  about 90 minutes, ~10 MB. Safe to stop (Ctrl-C) and resume later.")
    if ask_yes("Fetch?"):
        run([VENV_PY, "jpal.py", "--fetch"])


def check_gdl():
    ok = any((DATA / "gdl").glob("*.csv"))
    return ok, "file present" if ok else "not set up (optional; regions within countries)"


def install_gdl():
    say("Global Data Lab needs a free account, so this one is by hand:\n"
        "  1. Sign up and log in at https://globaldatalab.org\n"
        "  2. At https://globaldatalab.org/shdi/download/ download the CSV with all countries, years,\n"
        "     and indicators\n"
        f"  3. Save it into {DATA / 'gdl'}/")
    (DATA / "gdl").mkdir(parents=True, exist_ok=True)


def check_charts():
    ok = ASK_PY.exists()
    return ok, "Ask set up" if ok else "simple SVG charts only (optional: PNG charts, maps, animations)"


def install_charts():
    say("Gives the bundled Ask charting language its own environment: pandas, matplotlib, geopandas\n"
        "  from PyPI, about 300 MB.")
    if ask_yes("Install?"):
        if ASK_PY.exists() or run([sys.executable, "-m", "venv", ".venv"], cwd=ASK_DIR):
            run([ASK_PY, "-m", "pip", "install", "-e", ".[geo]"], cwd=ASK_DIR)


def check_offline():
    has = ollama_has("phi3.5")
    if has is None:
        return False, "Ollama not installed (optional; answers without internet, from the papers)"
    return has, "Ollama with phi3.5" if has else "Ollama installed, phi3.5 not pulled"


def install_offline():
    if ollama_has("phi3.5") is None:
        say("Install Ollama from https://ollama.com/download (the app starts its server),\n"
            "  then run this step again to download phi3.5.")
        return
    say("Downloads phi3.5 (Microsoft, ~2.2 GB) through Ollama. Needs ~4 GB free memory when answering.")
    if ask_yes("Download?"):
        run(["ollama", "pull", "phi3.5"])


def check_sandbox():
    ok = shutil.which("sandbox-exec") is not None
    return ok, "macOS sandbox available" if ok else "not macOS: run_python (custom calculations) is off"


STEPS = [  # name, needed, label, check, install
    ("python", True, "Python", check_python, None),
    ("packages", True, "Python packages", check_packages, install_packages),
    ("keys", True, "API keys", check_keys, install_keys),
    ("atlas", False, "Opportunity Atlas (US counties)", check_atlas, install_atlas),
    ("papers", False, "Paper library PDFs", check_papers, install_papers),
    ("index", False, "Paper search index", check_index, install_index),
    ("models", False, "Local models", check_models, install_models),
    ("longrun", False, "Long-run data (Maddison)", check_longrun, install_longrun),
    ("pwt", False, "Penn World Table", check_pwt, install_pwt),
    ("jpal", False, "J-PAL evaluations", check_jpal, install_jpal),
    ("gdl", False, "Global Data Lab (by hand)", check_gdl, install_gdl),
    ("charts", False, "Charts (Ask)", check_charts, install_charts),
    ("offline", False, "Offline answers (Ollama)", check_offline, install_offline),
    ("sandbox", False, "run_python sandbox", check_sandbox, None),
]


def report() -> list[str]:
    """Prints every step's status; returns the names of steps still to do."""
    todo = []
    say("Setup status\n")
    for name, needed, label, check, install in STEPS:
        try:
            ok, detail = check()
        except Exception as e:  # a broken check shouldn't hide the others
            ok, detail = False, f"couldn't check ({type(e).__name__}: {e})"
        mark = "✓" if ok else ("✗" if needed else "–")
        say(f"  {mark} {label:<34} {detail}")
        if not ok and install:
            todo.append(name)
    say("\n✗ = needed to start, – = optional.  World Bank, DHS and OpenAlex need no setup.")
    return todo


def main(argv: list[str]) -> int:
    if not argv:
        todo = report()
        say("\nNext: python3 setup_assistant.py install" if todo else
            "\nAll set. Start with:  .venv/bin/python web.py   (or ask.py for the terminal)")
        return 0
    if argv[0] != "install":
        say(__doc__)
        return 1
    names = [s[0] for s in STEPS]
    if argv[1:]:
        unknown = [a for a in argv[1:] if a not in names]
        if unknown:
            say(f"Unknown step(s): {', '.join(unknown)}. Steps: {', '.join(n for n in names)}")
            return 1
        todo = argv[1:]
    else:
        todo = report()
        if not check_python()[0]:
            say("\nInstall a newer Python first (python.org), then run this again with it.")
            return 1
    for name, _, label, _, install in STEPS:
        if name in todo and install:
            say(f"\n=== {label} ===")
            try:
                install()
            except KeyboardInterrupt:
                say("\n  (skipped)")
    say("\nDone. Run `python3 setup_assistant.py` any time to see what's set up.")
    return 0


def cli():  # the pyproject.toml command
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
