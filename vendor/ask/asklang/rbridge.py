"""The R bridge: `predict <target> from <a>, <b> using r package "X"`.

Fully optional - a plain `pip install -e .` never needs R, and every other
verb in Ask works without it. When Rscript isn't on PATH, `run_r_predict`
raises a teaching AskError rather than a traceback; when it is, this:

  1. writes the working columns to a temp CSV
  2. generates a small R script that loads the requested package and fits
     the same kind of model Ask's own `predict` would (`lm` for a numeric
     target, `glm(family = binomial)` for a yes/no one) - so a user can
     cross-check Ask's built-in regression against real R
  3. runs it via `Rscript` in a subprocess and parses a plain-text result
     block back out of stdout (deliberately not JSON/RDS, so parsing
     doesn't need another R package)

Never surfaces a raw R error or traceback - every failure path is an
AskError with a plain-English fix.
"""

import os
import re
import shutil
import subprocess
import tempfile

from .errors import AskError

_R_AVAILABLE = None
_MARKER = "ASK_R_RESULT"
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._]*$")


def r_available():
    """True if an Rscript executable is on PATH. Cached; never required to run."""
    global _R_AVAILABLE
    if _R_AVAILABLE is None:
        _R_AVAILABLE = shutil.which("Rscript") is not None
    return _R_AVAILABLE


def _check_identifier(name, what):
    # these come from parsed column names / a quoted package string, but
    # they get spliced straight into an R formula and a library() call, so
    # reject anything that isn't a plain identifier rather than pass it to
    # a shell/R parser and hope for the best.
    if not _NAME_RE.match(name):
        raise AskError(
            what=f"{what} {name!r} isn't a plain name R can use.",
            where="the predict step",
            fix="Use column/package names made of letters, numbers, '.', '_'.")


def generate_r_script(csv_path, target, inputs, interactions, package, is_binary,
                      marker=_MARKER):
    """Return the R source that fits the model and prints a parseable result
    block. A standalone function (not inlined into run_r_predict) so script
    generation can be unit-tested without ever invoking Rscript."""
    _check_identifier(package, "The R package")
    for name in [target] + inputs:
        _check_identifier(name, "The column")
    terms = list(inputs) + [f"{a}:{b}" for a, b in interactions]
    formula = f"{target} ~ {' + '.join(terms)}" if terms else f"{target} ~ 1"
    fit_call = (f'glm({formula!r}, data = df, family = binomial)' if is_binary
               else f'lm({formula!r}, data = df)')
    r2_line = ('s$r.squared' if not is_binary
              else '1 - model$deviance / model$null.deviance')  # McFadden-ish
    return f'''\
suppressWarnings(suppressMessages(library({package})))
df <- read.csv("{csv_path}")
model <- {fit_call}
s <- summary(model)
cat("{marker}_START\\n")
cat("rows:", nrow(model.frame(model)), "\\n")
r2 <- tryCatch({r2_line}, error = function(e) NA)
cat("r_squared:", if (is.null(r2)) NA else r2, "\\n")
coefs <- coef(s)
pcol <- ncol(coefs)
for (i in seq_len(nrow(coefs))) {{
  cat("coef:", rownames(coefs)[i], coefs[i, 1], coefs[i, 2], coefs[i, pcol], "\\n")
}}
cat("{marker}_END\\n")
'''


def parse_r_output(text, marker=_MARKER):
    """Parse the plain-text result block `generate_r_script` prints into
    {"rows": int, "r_squared": float|None,
     "coefs": [(name, estimate, se, pvalue), ...]}.
    Raises AskError if the expected markers aren't present."""
    start = text.find(f"{marker}_START")
    end = text.find(f"{marker}_END")
    if start == -1 or end == -1:
        raise AskError(
            what="R ran but didn't produce a result Ask understands.",
            where="the predict step",
            fix="Try predict <target> from <inputs> without 'using r package' "
                "to use Ask's own regression.")

    def _num(v):
        return None if v in ("NA", "NaN", "") else float(v)

    rows, r_squared, coefs = None, None, []
    for line in text[start:end].splitlines():
        line = line.strip()
        if line.startswith("rows:"):
            rows = int(float(line.split(":", 1)[1].strip()))
        elif line.startswith("r_squared:"):
            r_squared = _num(line.split(":", 1)[1].strip())
        elif line.startswith("coef:"):
            parts = line.split()
            # parts = ["coef:", name, estimate, se, pvalue]
            name = parts[1]
            estimate, se, pvalue = _num(parts[2]), _num(parts[3]), _num(parts[4])
            coefs.append((name, estimate, se, pvalue))
    return {"rows": rows, "r_squared": r_squared, "coefs": coefs}


def run_r_predict(df, target, inputs, interactions, package, is_binary, line):
    """Fit `target ~ inputs (+ interactions)` in R via package `package`.
    Returns the dict `parse_r_output` produces. Every failure - R missing,
    the package missing, the model failing to fit - is an AskError."""
    if not r_available():
        raise AskError(
            what="R isn't installed - install it from r-project.org, or drop "
                 "'using r package' to run Ask's built-in regression.",
            where=f"line {line}",
            fix=f"predict {target} from {', '.join(inputs)}")
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "data.csv")
        cols = [target] + [c for c in inputs if c != target]
        df[cols].to_csv(csv_path, index=False)
        script = generate_r_script(csv_path, target, inputs, interactions,
                                   package, is_binary)
        script_path = os.path.join(tmp, "fit.R")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)
        try:
            result = subprocess.run(["Rscript", "--vanilla", script_path],
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise AskError(
                what="Couldn't run R.",
                where=f"line {line}",
                fix="Check that 'Rscript' works from a terminal, or drop "
                    "'using r package'.")
        stderr_l = (result.stderr or "").lower()
        if "there is no package called" in stderr_l:
            raise AskError(
                what=f"The R package {package!r} isn't installed.",
                where=f"line {line}",
                fix=f'In R, run: install.packages("{package}")')
        if result.returncode != 0 or f"{_MARKER}_START" not in result.stdout:
            tail = [l for l in (result.stderr or result.stdout).strip()
                    .splitlines() if l.strip()]
            hint = tail[-1] if tail else "R produced no output."
            raise AskError(
                what="R couldn't fit that model.",
                where=f"line {line}",
                fix=f"R said: {hint}")
        return parse_r_output(result.stdout)
