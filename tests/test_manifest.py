"""
Tests for data_manifest.py: the list of data files notices missing, new and
changed files, and tells refreshed caches apart. Temporary folder only, no
network, under a second:

    .venv/bin/python tests/test_manifest.py
"""

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import data_manifest as dm  # noqa: E402


def _folder():
    data = Path(tempfile.mkdtemp())
    (data / "atlas").mkdir()
    (data / "imf").mkdir()
    (data / "atlas" / "county_outcomes_simple.csv").write_text("a,b\n1,2\n")
    (data / "imf" / "LUR.json").write_text("{}")
    (data / "embeddings_BAAI_bge-small-en-v1.5.npy").write_bytes(b"\0" * 10)
    (data / "usage").mkdir()  # saved chats and token counts aren't data: never listed
    (data / "usage" / "2026-10-09.jsonl").write_text("{}")
    (data / "sessions.db").write_bytes(b"x")
    (data / "sessions.db-journal").write_bytes(b"x")
    return data


def test_manifest_lists_every_file_with_source_and_checksum():
    data = _folder()
    files = dm.write(data)
    saved = json.loads((data / "MANIFEST.json").read_text())["files"]
    assert saved == files and len(files) == 3, files
    assert files["imf/LUR.json"]["kind"] == "cache"
    assert files["atlas/county_outcomes_simple.csv"]["source"].startswith("Opportunity Insights")
    assert files["embeddings_BAAI_bge-small-en-v1.5.npy"]["kind"] == "generated"
    assert len(files["imf/LUR.json"]["sha256"]) == 64 and files["imf/LUR.json"]["bytes"] == 2


def test_check_finds_missing_new_and_changed_files():
    data = _folder()
    dm.write(data)
    assert dm.check(data) == {"missing": [], "new": [], "changed": [], "changed_cache": []}
    (data / "atlas" / "county_outcomes_simple.csv").write_text("a,b\n1,3\n")
    (data / "imf" / "LUR.json").write_text('{"x": 1}')
    (data / "embeddings_BAAI_bge-small-en-v1.5.npy").unlink()
    (data / "atlas" / "extra.csv").write_text("x")
    assert dm.check(data) == {"missing": ["embeddings_BAAI_bge-small-en-v1.5.npy"],
                              "new": ["atlas/extra.csv"],
                              "changed": ["atlas/county_outcomes_simple.csv"],
                              "changed_cache": ["imf/LUR.json"]}


if __name__ == "__main__":
    failed = 0
    for name, fn in [(n, f) for n, f in list(globals().items()) if n.startswith("test_")]:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}\n" + "".join(f"        {l}\n" for l in traceback.format_exc().splitlines()[-4:]))
    print(f"\n{failed} failed")
    sys.exit(1 if failed else 0)
