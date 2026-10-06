#!/usr/bin/env python3
"""Compatibility launcher for Ask.

The implementation lives in the asklang/ package (tokens, parser, expressions,
runtime, interpreter, stats, charts, report, codegen, ui, cli). This shim keeps
the old entry points working:

    python3 ask.py               # launch the UI
    python3 ask.py --selftest    # headless check
    import ask                   # re-exports the public API
"""

from asklang import (AskError, Env, Interpreter, SAMPLES, Table,   # noqa: F401
                     build_html_report, generate_code, load_table,
                     render_chart, run_program, save_chart_image, tokenize)
from asklang.cli import main

if __name__ == "__main__":
    main()
