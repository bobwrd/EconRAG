"""Ask — a beginner data-analysis language with a coding UI.

Public API:
    run_program(src, env)  -> (Table|None, Chart|None, notices, trace)
    Env, Interpreter, Table, AskError
    load_table, render_chart, save_chart_image, generate_code
    build_html_report, SAMPLES
"""

__version__ = "0.2.1"

from .charts import render_chart, save_chart_image           # noqa: F401
from .codegen import generate_code                           # noqa: F401
from .errors import AskError                                 # noqa: F401
from .interpreter import Env, Interpreter, run_program       # noqa: F401
from .report import build_html_report                        # noqa: F401
from .runtime import (Table, load_table, clear_load_cache,   # noqa: F401
                      register_upload, list_uploads, UPLOADS_DIR)
from .samples import SAMPLES, WELCOME                        # noqa: F401
from .tokens import tokenize                                 # noqa: F401
from .parser import Parser                                   # noqa: F401
