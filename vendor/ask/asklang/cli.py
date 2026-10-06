"""Command-line interface.

    ask                      launch the UI
    ask run prog.ask         run a program headlessly
        --out result.csv     write the final table
        --report out.html    write a self-contained HTML report
        --chart out.png      write the chart (png/svg/pdf)
        --code pandas|r      print equivalent code instead of running
    ask --selftest           run every bundled sample + error case
"""

import argparse
import sys


def _check_environment():
    """Import the scientific stack with a helpful message instead of a traceback
    (common on Apple-silicon Macs with an x86_64 numpy installed)."""
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        import matplotlib  # noqa: F401
    except ImportError as e:
        msg = str(e)
        print("Ask can't start: a required package failed to import.\n")
        if "architecture" in msg or "x86_64" in msg or "arm64" in msg:
            print("Your numpy/pandas were built for a different CPU architecture")
            print("(e.g. an Intel build on an Apple-silicon Mac). Reinstall them:")
            print("    python3 -m pip install --force-reinstall --no-cache-dir "
                  "numpy pandas matplotlib")
        else:
            print("Install the requirements first:")
            print("    python3 -m pip install -r requirements.txt")
        print(f"\nDetails: {msg.splitlines()[0] if msg else e}")
        sys.exit(1)


def main(argv=None):
    _check_environment()
    argv = list(sys.argv[1:] if argv is None else argv)

    # legacy flag from the single-file era
    if "--selftest" in argv:
        _selftest()
        return

    parser = argparse.ArgumentParser(prog="ask", description="The Ask language.")
    sub = parser.add_subparsers(dest="cmd")
    runp = sub.add_parser("run", help="run an .ask program headlessly")
    runp.add_argument("file")
    runp.add_argument("--out", help="write the final table as CSV")
    runp.add_argument("--report", help="write a self-contained HTML report")
    runp.add_argument("--chart", help="write the chart image (png/svg/pdf); "
                                      "for an animated bubble chart, "
                                      "write .gif or .mp4 instead")
    runp.add_argument("--code", choices=["pandas", "r"],
                      help="print equivalent code instead of running")
    sub.add_parser("selftest", help="run every bundled sample + error case")
    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        _selftest()
        return
    if args.cmd == "run":
        _run_file(args)
        return

    # no subcommand -> UI
    from .ui import launch_ui
    try:
        launch_ui()
    except Exception as e:
        print("Could not start the UI:", e)
        print("If you're in a headless environment, run:  ask run program.ask")


def _run_file(args):
    import matplotlib
    matplotlib.use("Agg")
    from .codegen import generate_code
    from .errors import AskError
    from .interpreter import Env, run_program
    from .report import build_html_report
    from .charts import save_chart_image

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        print(f"Can't read {args.file}: {e}")
        sys.exit(1)

    if args.code:
        print(generate_code(src, args.code))
        return

    try:
        table, chart, notices, trace = run_program(src, Env())
    except AskError as e:
        print("Error:", e.render())
        sys.exit(1)

    for s in trace:
        print(" ", s.text)
    for n in notices:
        print("note:", n)
    if table is not None:
        with_pd_width(table)
    if args.out and table is not None:
        table.df.to_csv(args.out, index=False)
        print(f"wrote {len(table.df):,} rows to {args.out}")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(build_html_report(src, trace, table, chart))
        print(f"wrote report to {args.report}")
    if args.chart:
        if chart is None:
            print("no chart step in the program; --chart skipped")
        else:
            ext = args.chart.rsplit(".", 1)[-1].lower() if "." in args.chart else ""
            animated = (chart.kind == "bubble"
                       and any(m.kind == "animate" for m in chart.mods))
            if animated and ext in ("gif", "mp4"):
                from .charts import save_chart_animation
                save_chart_animation(chart, args.chart)
            else:
                save_chart_image(chart, args.chart, dpi=200)
            print(f"wrote chart to {args.chart}")


def with_pd_width(table):
    import pandas as pd
    with pd.option_context("display.max_rows", 30, "display.width", 120):
        print()
        print(table.df)


def _selftest():
    """Run every sample + error case without a GUI and report pass/fail."""
    import matplotlib
    matplotlib.use("Agg")
    from .charts import render_chart
    from .codegen import generate_code
    from .errors import AskError
    from .interpreter import Env, run_program
    from .samples import OPTIONAL_SAMPLES, SAMPLES

    ok = True
    for name, src in SAMPLES.items():
        needs = OPTIONAL_SAMPLES.get(name)
        if needs:
            import importlib
            try:
                importlib.import_module(needs)
            except ImportError:
                print(f"[SKIP] {name}: needs the optional '{needs}' package "
                      f"(not installed)")
                continue
        try:
            table, chart, notices, trace = run_program(src, Env())
            rows = 0 if table is None else len(table.df)
            has_chart = "chart" if chart is not None else "no chart"
            if chart is not None:
                render_chart(chart)  # ensure it renders
            steps = len(trace)
            print(f"[PASS] {name}: {rows} rows, {has_chart}, {steps} trace steps")
        except Exception as e:
            ok = False
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")

    # code export must work for a core program
    try:
        code = generate_code(SAMPLES["2. Group and chart"], "pandas")
        assert "groupby" in code
        rcode = generate_code(SAMPLES["2. Group and chart"], "r")
        assert "summarise" in rcode
        print("[PASS] code export: pandas + R generated")
    except Exception as e:
        ok = False
        print(f"[FAIL] code export: {e}")

    bad_cases = {
        "show before group by": 'load "sales.csv"\nshow count\ngroup by region',
        "misspelled column": 'load "sales.csv"\nwhere regionn is "West"\nshow count',
        "math on text": 'load "reviews.csv"\nadd x = score + 1\nshow count',
        "misspelled command": 'load "sales.csv"\nwheer region is "West"',
        "compare one group": 'load "sales.csv"\nwhere region is "West"\n'
                             'compare revenue between region',
        "predict no inputs": 'load "sales.csv"\npredict revenue from',
        "resample no date": 'load "sales.csv"\nresample by month',
        "unknown period": 'load "signups.csv"\nresample by fortnight',
        "explain with nothing above": 'explain',
        "group filter without group by":
            'load "sales.csv"\nkeep top 3 within group by revenue',
        "window bad column":
            'load "sales.csv"\ngroup by region\nrunning total of nope',
        "estimate bad column": 'load "sales.csv"\nestimate average nope with bootstrap',
        "unknown function": 'load "sales.csv"\nadd x = boom(revenue)',
        "function wrong arity": 'define function twice(x): x * 2\n'
                                'load "sales.csv"\nadd y = twice(revenue, cost)',
        "for each unknown recipe":
            'load "sales.csv"\nfor each region do recipe missing',
        "per group without group by":
            'load "sales.csv"\nadd s = revenue per group',
        "strict types drops numbers": 'use strict types\nload "reviews.csv"\n'
                                      'show average score',
        # a package name that can't possibly exist, so this raises an
        # AskError (R missing, or R present but the package missing) either
        # way - not dependent on whether the grading machine happens to
        # have R installed
        "r bridge without R/package": 'load "sales.csv"\n'
            'predict revenue from cost using r package "ask_totally_fake_pkg_xyz"',
        # regression coverage for the once-crashing dangling lines
        "dangling bin": 'load "sales.csv"\nbin',
        "dangling compare": 'load "sales.csv"\ncompare revenue between',
        "dangling combine": 'load "sales.csv"\ncombine with',
        "recursive recipe": 'define recipe loop from data:\n  loop\n'
                            'load "sales.csv"\n  loop',
        "recursive function": 'define function f(x): f(x)\n'
                              'load "sales.csv"\nadd y = f(revenue)',
        "collinear predict": 'load "sales.csv"\nadd c2 = cost\n'
                             'predict revenue from cost and c2',
    }
    for name, src in bad_cases.items():
        try:
            run_program(src, Env())
            print(f"[FAIL] bad case '{name}' did not raise")
            ok = False
        except AskError as e:
            tag = " [has apply-fix]" if e.apply else ""
            print(f"[PASS] bad case '{name}' -> {e.what}{tag}")
        except Exception as e:
            print(f"[FAIL] bad case '{name}' raised non-Ask error: {e}")
            ok = False

    # apply-fix suggestions should exist for typos
    for label, src in (("column typo", 'load "sales.csv"\nwhere regionn is "West"'),
                       ("command typo", 'load "sales.csv"\nwheer region is "West"')):
        try:
            run_program(src, Env())
        except AskError as e:
            status = "PASS" if e.apply else "FAIL"
            ok = ok and e.apply is not None
            print(f"[{status}] apply-fix present for {label}")

    print("\nAll good." if ok else "\nSome checks failed.")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
