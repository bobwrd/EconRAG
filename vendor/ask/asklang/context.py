"""Shared run context.

The running Interpreter registers itself here so low-level helpers (numeric
coercion, fuzzy matching, cached loads) can report what they quietly did
without threading a notices list through every function. Single-threaded per
run; the UI runs programs on one worker thread at a time.

`capture_events()` lets the table-load cache record load-time events (notices
and coercion drops) once, then replay them on every cache hit — so strict-mode
errors and "ignored N non-number values" notices still fire on cached loads.
"""

from contextlib import contextmanager

from .errors import AskError

interp = None       # the running Interpreter (or None)
_capture = None     # active event-capture list (or None)


def record_notice(msg):
    if _capture is not None:
        _capture.append(("note", (msg,)))
        return
    if interp is not None and msg not in interp.notices:
        interp.notices.append(msg)


def coerce_drop_report(dropped, cname, context, ln=None):
    """Report a silent numeric coercion. Under strict types, drops become errors."""
    if not dropped:
        return
    if _capture is not None:
        _capture.append(("drop", (dropped, cname, context, ln)))
        return
    if interp is not None and interp.env.strict:
        raise AskError(
            what=f"The column {cname!r} has {dropped} value(s) that aren't numbers, "
                 f"and strict types is on.",
            where=f"line {ln}" if ln else "",
            fix=f"Clean or fill {cname} first, or drop the 'use strict types' line.",
            line=ln)
    record_notice(f"ignored {dropped} non-number value(s) in {cname} "
                  f"while {context}.")


@contextmanager
def capture_events():
    global _capture
    old = _capture
    _capture = []
    try:
        yield _capture
    finally:
        _capture = old


def replay_events(events):
    for kind, args in events:
        if kind == "note":
            record_notice(*args)
        else:
            coerce_drop_report(*args)
