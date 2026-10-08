"""
A list of the data files in data/: where each came from, its size, date and
SHA-256 checksum, saved as data/MANIFEST.json. Standard library only, no network.

    .venv/bin/python data_manifest.py           # write data/MANIFEST.json and print a summary
    .venv/bin/python data_manifest.py --check   # compare data/ with the saved list

"Cache" files are refreshed by the program on its own (World Bank catalog, IMF for a
week, DHS catalog), so their changes are expected; anything else that changes was
re-downloaded or edited.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST = DATA / "MANIFEST.json"

# first part of the path inside data/ -> (source, kind)
SOURCES = {
    "atlas": ("Opportunity Insights, Opportunity Atlas (+ census.gov county names)", "downloaded once"),
    "dhs": ("DHS Program API catalog", "cache"),
    "gdl": ("Global Data Lab, subnational HDI (saved by hand)", "downloaded once"),
    "imf": ("IMF DataMapper, World Economic Outlook", "cache"),
    "jpal": ("J-PAL evaluation summaries (jpal.py --fetch)", "downloaded once"),
    "longrun": ("Maddison Project (via Our World in Data) and Penn World Table 11.0 (via FRED)",
                "downloaded once"),
    "worldbank": ("World Bank indicator and country catalogs", "cache"),
    "chunks.json": ("ingest.py, from the PDFs in docs/", "generated"),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def describe(path: Path, data: Path) -> dict:
    top = path.relative_to(data).parts[0]
    source, kind = SOURCES.get(top, ("ingest.py (embeddings)", "generated") if top.startswith("embeddings_")
                               else ("unknown", "unknown"))
    stat = path.stat()
    return {"source": source, "kind": kind, "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sha256": sha256(path)}


def scan(data: Path = DATA) -> dict:
    files = sorted(p for p in data.rglob("*") if p.is_file() and p.name not in ("MANIFEST.json", ".DS_Store"))
    return {str(p.relative_to(data)): describe(p, data) for p in files}


def write(data: Path = DATA) -> dict:
    files = scan(data)
    (data / "MANIFEST.json").write_text(json.dumps(
        {"written": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "files": files}, indent=2) + "\n")
    return files


def check(data: Path = DATA) -> dict:
    """{'missing': [...], 'new': [...], 'changed': [...], 'changed_cache': [...]} vs the saved list."""
    files = scan(data)
    saved = json.loads((data / "MANIFEST.json").read_text())["files"]
    changed = [k for k in files if k in saved and files[k]["sha256"] != saved[k]["sha256"]]
    return {"missing": [k for k in saved if k not in files],
            "new": [k for k in files if k not in saved],
            "changed": [k for k in changed if files[k]["kind"] != "cache"],
            "changed_cache": [k for k in changed if files[k]["kind"] == "cache"]}


def main(argv: list[str]) -> int:
    if not DATA.is_dir():
        print("No data/ folder yet (see setup_assistant.py).")
        return 1
    if "--check" in argv:
        if not MANIFEST.exists():
            print("No data/MANIFEST.json yet: run `python data_manifest.py` first.")
            return 1
        result = check()
        labels = {"missing": "Missing", "new": "New (not in the list)", "changed": "Changed",
                  "changed_cache": "Refreshed caches (expected)"}
        for key, label in labels.items():
            for name in result[key]:
                print(f"{label}: {name}")
        problems = result["missing"] + result["new"] + result["changed"]
        print("All data files match the list." if not problems else
              f"{len(problems)} difference(s). Run `python data_manifest.py` to accept them as the new list.")
        return 1 if problems else 0
    files = write()
    for name, f in files.items():
        print(f"{f['bytes'] / 1e6:7.1f} MB  {f['modified'][:10]}  {f['kind']:15}  {name}")
    print(f"\n{len(files)} files, {sum(f['bytes'] for f in files.values()) / 1e6:.0f} MB. Saved data/MANIFEST.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
