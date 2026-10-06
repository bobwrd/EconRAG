"""Runtime: Table (DataFrame + algebraic column types), type inference, loading.

Path resolution (bug E2): relative paths resolve against the CURRENT WORKING
DIRECTORY first, then the uploads folder (see below), then the repo/sample
directory so the bundled sample programs keep working from anywhere. Writes
always go to the cwd.

Uploads: the UI's "Upload data..." picker copies chosen files into a stable,
per-user folder (~/Library/Application Support/Ask/uploads on macOS, an
XDG-style path elsewhere) rather than referencing them at their original
location. That way a `load "name.csv"` line keeps working across sessions
even if the original file gets moved, renamed, or lives on a USB drive that
isn't always plugged in - the point of "uploading" rather than "browsing".

Loads are cached by (path, mtime, size) so the UI's live keystroke preview
doesn't re-read files from disk. Load-time events (encoding notices, numeric
coercion drops) are recorded once and replayed on every cache hit, so strict
mode and the notice strip behave identically whether the load was cached.
"""

import csv
import filecmp
import io
import os
import re
import shutil
import sys

import pandas as pd

from . import context
from .errors import AskError

# The directory holding the bundled sample CSVs: sample_data/ next to this
# package when running from a source checkout.
SAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")

CURRENCY_RE = re.compile(r"^\s*[-+]?[$£€]\s?[\d,]+(\.\d+)?\s*$")


def _default_uploads_dir():
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/Ask")
    elif os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA",
                            os.path.expanduser("~")), "Ask")
    else:
        base = os.path.join(os.environ.get("XDG_DATA_HOME",
                            os.path.expanduser("~/.local/share")), "ask")
    return os.path.join(base, "uploads")


UPLOADS_DIR = _default_uploads_dir()


class Table:
    def __init__(self, df, coltypes=None):
        self.df = df.reset_index(drop=True)
        self.coltypes = coltypes or {}

    def copy(self, df=None, coltypes=None):
        t = Table(df if df is not None else self.df.copy(),
                  dict(coltypes if coltypes is not None else self.coltypes))
        if hasattr(self, "_group_keys"):
            t._group_keys = self._group_keys
        return t


def resolve_read_path(path):
    """Resolve a path for reading: absolute as-is; else cwd, then the uploads
    folder, then the bundled-samples dir."""
    if os.path.isabs(path):
        return path
    cwd_path = os.path.abspath(path)
    if os.path.exists(cwd_path):
        return cwd_path
    uploaded = os.path.join(UPLOADS_DIR, path)
    if os.path.exists(uploaded):
        return uploaded
    fallback = os.path.join(SAMPLES_DIR, path)
    if os.path.exists(fallback):
        return fallback
    return cwd_path  # let the caller report "not found" against the cwd


def register_upload(src_path, uploads_dir=None):
    """Copy a file the user picked into the uploads folder, returning the name
    to use in `load "name"`. Re-uploading the identical file is a no-op;
    uploading a different file that happens to share a name gets a Finder-style
    ' (2)' suffix so nothing is silently overwritten."""
    uploads_dir = uploads_dir or UPLOADS_DIR
    os.makedirs(uploads_dir, exist_ok=True)
    base = os.path.basename(src_path)
    name, ext = os.path.splitext(base)
    dest = os.path.join(uploads_dir, base)
    i = 1
    while os.path.exists(dest) and not filecmp.cmp(dest, src_path, shallow=False):
        i += 1
        dest = os.path.join(uploads_dir, f"{name} ({i}){ext}")
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return os.path.basename(dest)


def list_uploads(uploads_dir=None):
    """Names of every file currently in the uploads folder, newest first."""
    uploads_dir = uploads_dir or UPLOADS_DIR
    if not os.path.isdir(uploads_dir):
        return []
    entries = [f for f in os.listdir(uploads_dir)
              if os.path.isfile(os.path.join(uploads_dir, f))
              and not f.startswith(".")]
    entries.sort(key=lambda f: os.path.getmtime(os.path.join(uploads_dir, f)),
                reverse=True)
    return entries


def resolve_write_path(path):
    """Resolve a path for writing: absolute as-is; else the cwd."""
    return path if os.path.isabs(path) else os.path.abspath(path)


def infer_types(df):
    """Auto-detect an algebraic type per column; parse currency/number/date in place."""
    coltypes = {}
    for col in df.columns:
        s = df[col]
        non_null = s.dropna().astype(str)
        if len(non_null) == 0:
            coltypes[col] = "Text"
            continue
        # Money: values like $1,200.00
        if non_null.str.match(CURRENCY_RE).mean() > 0.8:
            df[col] = pd.to_numeric(
                s.astype(str).str.replace(r"[^\d.\-]", "", regex=True),
                errors="coerce")
            coltypes[col] = "Money"
            continue
        # Percent: values like 45% or 12.5%
        if non_null.str.match(r"^\s*-?\d+(\.\d+)?\s*%\s*$").mean() > 0.8:
            df[col] = pd.to_numeric(
                non_null.str.replace("%", "", regex=False),
                errors="coerce").reindex(s.index)
            coltypes[col] = "Percent"
            continue
        # already numeric
        if pd.api.types.is_numeric_dtype(s):
            coltypes[col] = "Number"
            continue
        # try numeric
        conv = pd.to_numeric(s, errors="coerce")
        if conv.notna().mean() > 0.9:
            dropped = int((s.notna() & conv.isna()).sum())
            context.coerce_drop_report(dropped, col, "reading this column as numbers")
            df[col] = conv
            coltypes[col] = "Number"
            continue
        # boolean
        low = non_null.str.lower()
        if set(low.unique()) <= {"true", "false", "yes", "no"}:
            df[col] = low.isin(["true", "yes"]).reindex(s.index)
            coltypes[col] = "Boolean"
            continue
        # date
        if _looks_like_date(non_null):
            df[col] = pd.to_datetime(s, errors="coerce")
            coltypes[col] = "Date"
            continue
        coltypes[col] = "Category" if non_null.nunique() <= max(20, len(non_null) // 20) else "Text"
    return coltypes


def _looks_like_date(non_null):
    sample = non_null.head(30)
    if not sample.str.match(r"^\d{2,4}[-/]\d{1,2}[-/]\d{1,4}").any():
        return False
    parsed = pd.to_datetime(sample, errors="coerce")
    return parsed.notna().mean() > 0.8


# ---- cached loading --------------------------------------------------------

_LOAD_CACHE = {}   # resolved path -> (stat_key, Table, [events])
_CACHE_MAX = 8


def clear_load_cache():
    _LOAD_CACHE.clear()


def load_table(path):
    resolved = resolve_read_path(path)
    if not os.path.exists(resolved):
        raise AskError(
            what=f"I can't find the file {path!r}.",
            where="",
            fix=f"Put {path!r} in your working folder (or use its full path), "
                f"or pick a sample from the dropdown.")
    try:
        st = os.stat(resolved)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    hit = _LOAD_CACHE.get(resolved)
    if key is not None and hit is not None and hit[0] == key:
        _, cached, events = hit
    else:
        with context.capture_events() as events:
            cached = _load_uncached(resolved, path)
        if key is not None:
            if len(_LOAD_CACHE) >= _CACHE_MAX:
                _LOAD_CACHE.pop(next(iter(_LOAD_CACHE)))
            _LOAD_CACHE[resolved] = (key, cached, list(events))
    # replay load-time events every time (notices + strict-mode drops)
    context.replay_events(events)
    return Table(cached.df.copy(), dict(cached.coltypes))


def _load_uncached(resolved, path):
    ext = os.path.splitext(resolved)[1].lower()
    if ext in (".xlsx", ".xls"):
        try:
            df = pd.read_excel(resolved)
        except Exception:
            raise AskError(
                what=f"I couldn't open the spreadsheet {path!r}.",
                where="",
                fix="Make sure it's a real .xlsx file, or export it to CSV first.")
    elif ext == ".json":
        try:
            df = pd.read_json(resolved)
        except Exception:
            raise AskError(what=f"I couldn't read the JSON in {path!r}.", where="",
                           fix="Check the file is valid JSON, or export it to CSV.")
    else:
        df = _read_delimited(resolved, path)
    df.columns = [str(c).strip() for c in df.columns]
    coltypes = infer_types(df)
    return Table(df, coltypes)


def _read_delimited(resolved, path):
    """Read a CSV/TSV, detecting delimiter and encoding, with teaching errors."""
    raw = None
    used_enc = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(resolved, "r", encoding=enc, newline="") as f:
                raw = f.read()
            used_enc = enc
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if raw is None:
        raise AskError(
            what=f"I couldn't read the text in {path!r}.",
            where="",
            fix="Re-save the file as UTF-8 (or plain CSV) and try again.")
    if not raw.strip():
        raise AskError(what=f"The file {path!r} looks empty.", where="",
                       fix="Pick a file that has a header row and some data.")
    sample = raw[:4096]
    delim = ","
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except Exception:
        counts = {d: sample.count(d) for d in ("\t", ";", "|")}
        best = max(counts, key=counts.get)
        if counts[best] > sample.count(","):
            delim = best
    try:
        df = pd.read_csv(io.StringIO(raw), sep=delim)
    except Exception:
        raise AskError(
            what=f"I couldn't make sense of the rows in {path!r}.",
            where="",
            fix="Check that every row has the same number of columns, then retry.")
    if used_enc not in ("utf-8", "utf-8-sig"):
        context.record_notice(f"read {path!r} as {used_enc} text (not plain UTF-8).")
    if delim != ",":
        nice = {"\t": "tab", ";": "semicolon", "|": "pipe"}.get(delim, delim)
        context.record_notice(f"detected a {nice}-separated file in {path!r}.")
    return df
