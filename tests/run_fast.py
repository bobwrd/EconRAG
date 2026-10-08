"""
Runs the quick test files one after another (zero Groq tokens, no models loaded,
under a minute or so). Each file is a plain script with its own PASS/FAIL list;
this just runs them and sums up. No pytest needed.

    .venv/bin/python tests/run_fast.py          # all quick tests (need data/ and some network)
    .venv/bin/python tests/run_fast.py --ci     # only those that need no data/ files (GitHub Actions)

Left out: the tests of single data sources (test_dhs, test_gdl, test_imf, test_jpal,
test_longrun) — run those after changing that source.
"""

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

NO_DATA = ["test_devecon", "test_manifest", "test_setup"]  # formulas; temp folders only
NEEDS_DATA = ["test_compute", "test_tools", "test_web", "test_reports"]  # data/ files, World Bank/FRED


def main(argv: list[str]) -> int:
    names = NO_DATA if "--ci" in argv else NO_DATA + NEEDS_DATA
    failed = []
    for name in names:
        start = time.time()
        print(f"=== {name}", flush=True)
        code = subprocess.run([sys.executable, str(HERE / f"{name}.py")]).returncode
        print(f"=== {name}: {'ok' if code == 0 else 'FAILED'} ({time.time() - start:.0f}s)\n", flush=True)
        if code:
            failed.append(name)
    print(f"{len(names) - len(failed)} of {len(names)} test files passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
