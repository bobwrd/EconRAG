"""The tkinter coding UI: editor, Run, Table/Chart/Steps/Files tabs, messages.

Improvements over the original single-file app:
  * programs run on a worker thread — the window never freezes (E3)
  * the live shape preview is debounced and loads are cached (E1)
  * Save chart... (PNG/SVG/PDF) and Export table... (CSV/XLSX) buttons (B4)
  * Open/Save program dialogs (E2/E7)
  * Upload data... accepts multiple files at once, copies them into a stable
    per-user folder, and lists them in a "Files" tab you can insert `load`
    lines from without ever browsing the filesystem again — the point of an
    "app" rather than a script you point at a folder
  * line numbers + lightweight syntax highlighting (E7)
  * a copy button on the Show-code window (E8)
  * cell formatting handles numpy scalars, bools, dates, infinities (E4)
"""

import math
import os
import queue
import threading

import numpy as np
import pandas as pd

from .codegen import generate_code
from .charts import (render_chart, render_chart_animation, save_chart_animation,
                     save_chart_image)
from .errors import AskError
from .interpreter import Env, Interpreter, run_program
from .parser import Parser
from .astnodes import Save, SaveReport
from .runtime import Table, UPLOADS_DIR, list_uploads, register_upload
from .samples import SAMPLES, WELCOME
from .tokens import tokenize

_KEYWORDS = (
    "load|from|save|report|understand|clean|standardize|fill|missing|drop|"
    "duplicates|flag|outliers|keep|rename|add|bin|split|replace|where|combine|"
    "stack|group|by|show|reshape|resample|sort|top|bottom|first|last|chart|"
    "type|define|recipe|function|explain|compare|between|relate|predict|import|"
    "use|strict|types|set|seed|call|it|for|each|do|estimate|with|bootstrap|"
    "running|moving|rank|lag|lead|as|into|on|fuzzy|color|label|is|not|and|or|"
    "in|contains|starts|ends|per|within|descending|extract|after|before|"
    "number|if|then|else|let|total|average|count|min|max|median|spread|share|"
    "mode|skew|kurtosis|quantile")


def launch_ui():
    import tkinter as tk
    from tkinter import filedialog, ttk
    from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                                   NavigationToolbar2Tk)

    root = tk.Tk()
    root.title("Ask - a language for asking your data questions")
    root.geometry("1180x720")
    root.configure(bg="#EFE9DF")

    # Force a light, colour-honouring theme. macOS "aqua" ignores most colour
    # settings and follows the system dark/light mode, which can make text
    # invisible; "clam" renders our colours consistently on every platform.
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", background="#EFE9DF", foreground="#1A1A1A")
    style.configure("TFrame", background="#EFE9DF")
    style.configure("TLabel", background="#EFE9DF", foreground="#1A1A1A")
    style.configure("Treeview", background="white", foreground="#1A1A1A",
                    fieldbackground="white", rowheight=22)
    style.configure("Treeview.Heading", background="#DCD3C4",
                    foreground="#1A1A1A", font=("Helvetica", 10, "bold"))
    style.configure("TNotebook", background="#EFE9DF")
    style.configure("TNotebook.Tab", foreground="#1A1A1A", padding=(12, 4))
    style.map("Treeview", background=[("selected", "#4C72B0")],
              foreground=[("selected", "white")])

    state = {"table": None, "chart": None, "anim": None, "running": False,
             "preview_job": None, "program_path": None}
    results = queue.Queue()

    # --- top bar ---
    topbar = ttk.Frame(root, padding=(8, 6))
    topbar.pack(side="top", fill="x")
    ttk.Label(topbar, text="Ask", font=("Helvetica", 15, "bold")).pack(side="left")

    def small_btn(parent, text, bg="#3A4A63"):
        b = tk.Label(parent, text=text, font=("Helvetica", 11, "bold"),
                     bg=bg, fg="white", padx=10, pady=4, cursor="hand2")
        hover = _darken(bg)
        b.bind("<Enter>", lambda e: b.configure(bg=hover))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    open_btn = small_btn(topbar, "Open…", "#5A5A5A")
    open_btn.pack(side="left", padx=(14, 4))
    save_prog_btn = small_btn(topbar, "Save", "#5A5A5A")
    save_prog_btn.pack(side="left", padx=4)
    open_data_btn = small_btn(topbar, "Upload data…", "#5A5A5A")
    open_data_btn.pack(side="left", padx=4)

    sample_var = tk.StringVar(value="Load sample...")
    sample_menu = ttk.Combobox(topbar, textvariable=sample_var, state="readonly",
                               width=28, values=list(SAMPLES.keys()))
    sample_menu.pack(side="right")
    ttk.Label(topbar, text="Load sample:").pack(side="right", padx=(0, 6))

    code_btn = small_btn(topbar, "</>  Show code")
    code_btn.pack(side="right", padx=(0, 14))
    export_table_btn = small_btn(topbar, "Export table…", "#31708E")
    export_table_btn.pack(side="right", padx=(0, 8))
    save_chart_btn = small_btn(topbar, "Save chart…", "#31708E")
    save_chart_btn.pack(side="right", padx=(0, 8))

    # --- main split ---
    main = ttk.Panedwindow(root, orient="horizontal")
    main.pack(side="top", fill="both", expand=True)

    left = ttk.Frame(main)
    right = ttk.Frame(main)
    main.add(left, weight=3)
    main.add(right, weight=4)

    # editor + line-number gutter
    edframe = ttk.Frame(left)
    edframe.pack(side="top", fill="both", expand=True)
    gutter = tk.Text(edframe, width=4, padx=4, takefocus=0, bd=0,
                     bg="#E7E0D3", fg="#8A8272", font=("Menlo", 12),
                     state="disabled", wrap="none")
    gutter.pack(side="left", fill="y")
    editor = tk.Text(edframe, wrap="none", font=("Menlo", 12), undo=True,
                     bg="#FBF7F0", fg="#1A1A1A", insertbackground="#1A1A1A",
                     selectbackground="#CDE1F0", relief="flat", padx=8, pady=8)
    editor.pack(side="left", fill="both", expand=True)
    editor.insert("1.0", WELCOME)

    editor.tag_configure("kw", foreground="#1D4ED8")
    editor.tag_configure("str", foreground="#B45309")
    editor.tag_configure("num", foreground="#047857")
    editor.tag_configure("comment", foreground="#9CA3AF")

    import re as _re
    kw_re = _re.compile(r"\b(" + _KEYWORDS + r")\b")

    def highlight():
        src = editor.get("1.0", "end-1c")
        for tag in ("kw", "str", "num", "comment"):
            editor.tag_remove(tag, "1.0", "end")
        if len(src) > 20000:
            return
        for lineno, line in enumerate(src.split("\n"), start=1):
            ci = line.find("#")
            if ci >= 0:
                editor.tag_add("comment", f"{lineno}.{ci}", f"{lineno}.end")
                line = line[:ci]
            for m in _re.finditer(r'"[^"]*"', line):
                editor.tag_add("str", f"{lineno}.{m.start()}", f"{lineno}.{m.end()}")
            for m in kw_re.finditer(line):
                editor.tag_add("kw", f"{lineno}.{m.start()}", f"{lineno}.{m.end()}")
            for m in _re.finditer(r"\b\d+(\.\d+)?\b", line):
                editor.tag_add("num", f"{lineno}.{m.start()}", f"{lineno}.{m.end()}")

    def update_gutter():
        n = int(editor.index("end-1c").split(".")[0])
        gutter.configure(state="normal")
        gutter.delete("1.0", "end")
        gutter.insert("1.0", "\n".join(f"{i:>3}" for i in range(1, n + 1)))
        gutter.configure(state="disabled")
        gutter.yview_moveto(editor.yview()[0])

    def _sync_gutter(*_):
        gutter.yview_moveto(editor.yview()[0])

    editor.configure(yscrollcommand=lambda *a: _sync_gutter())

    # A Label-as-button: macOS native tk.Button ignores bg/fg, but Label honours them.
    run_btn = tk.Label(left, text="Run  ▶", font=("Helvetica", 13, "bold"),
                       bg="#2E7D32", fg="white", pady=9, cursor="hand2")
    run_btn.pack(side="top", fill="x", pady=(4, 0))
    run_btn.bind("<Enter>", lambda e: run_btn.configure(
        bg="#256428" if not state["running"] else "#8A8A8A"))
    run_btn.bind("<Leave>", lambda e: run_btn.configure(
        bg="#2E7D32" if not state["running"] else "#8A8A8A"))

    # One-click "Apply fix" - shown only when the last error carries a fix.
    apply_btn = tk.Label(left, text="Apply suggested fix", font=("Helvetica", 11, "bold"),
                         bg="#B9770E", fg="white", pady=6, cursor="hand2")
    apply_btn.bind("<Enter>", lambda e: apply_btn.configure(bg="#9C6309"))
    apply_btn.bind("<Leave>", lambda e: apply_btn.configure(bg="#B9770E"))

    msgframe = ttk.Frame(left)
    msgframe.pack(side="top", fill="x")
    messages = tk.Text(msgframe, height=7, wrap="word", font=("Menlo", 10),
                       bg="#F3EFE7", fg="#333333", relief="flat", padx=8, pady=6)
    msg_sb = ttk.Scrollbar(msgframe, orient="vertical", command=messages.yview)
    messages.configure(state="disabled", yscrollcommand=msg_sb.set)
    messages.pack(side="left", fill="both", expand=True)
    msg_sb.pack(side="right", fill="y")

    # Live pipeline-shape preview at the cursor line.
    shape_var = tk.StringVar(value="shape: -")
    ttk.Label(left, textvariable=shape_var, font=("Menlo", 10),
              anchor="w", padding=(8, 3)).pack(side="top", fill="x")

    # --- right: notebook with Table + Chart + Steps ---
    notebook = ttk.Notebook(right)
    notebook.pack(fill="both", expand=True)

    table_tab = ttk.Frame(notebook)
    chart_tab = ttk.Frame(notebook)
    steps_tab = ttk.Frame(notebook)
    files_tab = ttk.Frame(notebook)
    notebook.add(table_tab, text="Table")
    notebook.add(chart_tab, text="Chart")
    notebook.add(steps_tab, text="Steps")
    notebook.add(files_tab, text="Files")

    tree = ttk.Treeview(table_tab, show="headings")
    vsb = ttk.Scrollbar(table_tab, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(table_tab, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    table_status = ttk.Label(table_tab, text="", font=("Menlo", 10), padding=(6, 2))
    table_status.grid(row=2, column=0, sticky="ew")
    table_tab.rowconfigure(0, weight=1)
    table_tab.columnconfigure(0, weight=1)

    steps_text = tk.Text(steps_tab, wrap="word", font=("Menlo", 11), bg="white",
                         fg="#1A1A1A", relief="flat", padx=12, pady=10)
    steps_text.configure(state="disabled")
    steps_text.pack(fill="both", expand=True)

    ttk.Label(files_tab, text="Files you've uploaded — double-click to insert "
                             "a load line, or right-click to remove.",
              padding=(10, 8), wraplength=520).pack(side="top", fill="x")
    files_list = ttk.Treeview(files_tab, show="headings",
                              columns=("name", "size", "modified"))
    for col, w in (("name", 260), ("size", 90), ("modified", 160)):
        files_list.heading(col, text=col.capitalize())
        files_list.column(col, width=w, anchor="w")
    files_list.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    ttk.Label(files_tab, text=f"Stored in: {UPLOADS_DIR}",
              font=("Menlo", 9), foreground="#777", padding=(10, 4)
              ).pack(side="bottom", fill="x")

    pending_fix = {"apply": None}

    # --- helpers ---
    def set_messages(text, error=False):
        messages.configure(state="normal")
        messages.delete("1.0", "end")
        messages.insert("1.0", text)
        messages.configure(state="disabled", fg="#B00020" if error else "#333")

    def show_apply(apply):
        pending_fix["apply"] = apply
        if apply:
            apply_btn.pack(side="top", fill="x", after=run_btn)
        else:
            apply_btn.pack_forget()

    def fill_table(table):
        tree.delete(*tree.get_children())
        df = table.df
        cols = list(df.columns)
        tree["columns"] = cols
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=max(90, min(240, int(len(str(col)) * 9) + 40)),
                        anchor="w")
        shown = min(len(df), 500)
        for _, row in df.head(500).iterrows():
            values = [fmt_cell(row[c], table.coltypes.get(c)) for c in cols]
            tree.insert("", "end", values=values)
        if len(df) > shown:
            table_status.configure(
                text=f"showing {shown:,} of {len(df):,} rows - use "
                     f"'Export table…' for everything")
        else:
            table_status.configure(text=f"{len(df):,} rows, {len(cols)} columns")

    def fill_steps(trace):
        steps_text.configure(state="normal")
        steps_text.delete("1.0", "end")
        if not trace:
            steps_text.insert("end", "Run a program and every step is explained "
                                     "here, in order.")
        else:
            steps_text.insert("end", "What this program did, step by step:\n\n")
            for i, s in enumerate(trace, 1):
                steps_text.insert("end", f"{i}.  {s.text}\n")
        steps_text.configure(state="disabled")

    def draw_chart(chart):
        for widget in chart_tab.winfo_children():
            widget.destroy()
        state["anim"] = None
        animated = (chart.kind == "bubble"
                   and any(m.kind == "animate" for m in chart.mods))
        if animated:
            fig, anim = render_chart_animation(chart)
            state["anim"] = anim  # keep a reference so it doesn't get GC'd mid-play
        else:
            fig = render_chart(chart)
        canvas = FigureCanvasTkAgg(fig, master=chart_tab)
        canvas.draw()
        try:
            toolbar = NavigationToolbar2Tk(canvas, chart_tab, pack_toolbar=False)
            toolbar.update()
            toolbar.pack(side="bottom", fill="x")
        except Exception:
            pass
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def clear_chart():
        for widget in chart_tab.winfo_children():
            widget.destroy()
        state["anim"] = None

    def do_apply_fix(event=None):
        ap = pending_fix["apply"]
        if not ap:
            return
        ln = ap["line"]
        if ap["mode"] == "replace":
            editor.delete(f"{ln}.0", f"{ln}.end")
            editor.insert(f"{ln}.0", ap["text"])
        else:  # insert a new line before line `ln`
            editor.insert(f"{ln}.0", ap["text"] + "\n")
        show_apply(None)
        on_run()

    apply_btn.bind("<Button-1>", do_apply_fix)

    def show_code(event=None):
        src = editor.get("1.0", "end")
        pcode = generate_code(src, "pandas")
        rcode = generate_code(src, "r")
        win = tk.Toplevel(root)
        win.title("Equivalent code - the graduation bridge")
        win.geometry("680x620")
        win.configure(bg="#EFE9DF")
        head = ttk.Frame(win)
        head.pack(fill="x")
        ttk.Label(head, text="Here's what Ask just did, in real code:",
                  font=("Helvetica", 12, "bold"), padding=(10, 8)).pack(side="left")
        full = ("# ===== pandas (Python) =====\n" + pcode +
                "\n\n# ===== R (dplyr) =====\n" + rcode)

        def copy_code(_e=None):
            win.clipboard_clear()
            win.clipboard_append(full)
            copy_b.configure(text="Copied ✓")
            win.after(1200, lambda: copy_b.configure(text="Copy"))
        copy_b = small_btn(head, "Copy")
        copy_b.pack(side="right", padx=10)
        copy_b.bind("<Button-1>", copy_code)
        box = tk.Text(win, wrap="none", font=("Menlo", 11), bg="#FBF7F0",
                      fg="#1A1A1A", relief="flat", padx=10, pady=8)
        box.insert("end", full)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    code_btn.bind("<Button-1>", show_code)

    # --- threaded run (E3): the worker computes, the main thread paints ---
    def on_run(event=None):
        if state["running"]:
            return "break"
        src = editor.get("1.0", "end")
        state["running"] = True
        run_btn.configure(text="Running…", bg="#8A8A8A")

        def work():
            try:
                out = run_program(src, Env())
                results.put(("ok", out))
            except AskError as e:
                results.put(("askerr", e))
            except Exception as e:  # never surface a raw traceback
                results.put(("err", e))
        threading.Thread(target=work, daemon=True).start()
        root.after(50, poll_result)
        return "break"

    def poll_result():
        try:
            kind, payload = results.get_nowait()
        except queue.Empty:
            root.after(50, poll_result)
            return
        state["running"] = False
        run_btn.configure(text="Run  ▶", bg="#2E7D32")
        if kind == "ok":
            table, chart, notices, trace = payload
            state["table"], state["chart"] = table, chart
            note = "\n".join(notices) if notices else ""
            show_apply(None)
            fill_steps(trace)
            if table is not None:
                fill_table(table)
            if chart is not None:
                try:
                    draw_chart(chart)
                except AskError as e:
                    set_messages(e.render(), error=True)
                    return
                notebook.select(chart_tab)
            elif table is not None:
                clear_chart()
                notebook.select(table_tab)
            if table is None and chart is None:
                set_messages("Nothing to show yet. Start with a load line.")
            else:
                set_messages(note or "Ran successfully.")
        elif kind == "askerr":
            set_messages(payload.render(), error=True)
            show_apply(payload.apply)
        else:
            set_messages(f"Something went wrong: {payload}", error=True)
            show_apply(None)

    # --- debounced live shape preview (E1) ---
    def schedule_preview(event=None):
        highlight()
        update_gutter()
        if state["preview_job"] is not None:
            root.after_cancel(state["preview_job"])
        state["preview_job"] = root.after(400, update_shape)

    def update_shape():
        state["preview_job"] = None
        try:
            cur = int(editor.index("insert").split(".")[0])
            src = editor.get("1.0", "end")
            lines = [ln for ln in tokenize(src) if ln.lineno <= cur]
            if not lines:
                shape_var.set("shape: start with a load line")
                return
            # Skip disk-writing steps so keystroke previews have no side effects.
            clauses = [cl for cl in Parser(lines).parse_program()
                       if not isinstance(cl, (Save, SaveReport))]
            interp = Interpreter(Env())
            interp.raw_lines = {ln.lineno: ln.raw for ln in lines}
            interp.source = src
            t, _, _, _ = interp.run(clauses)
            if t is None:
                shape_var.set(f"shape at line {cur}: (no table yet)")
            else:
                shape_var.set(f"shape at line {cur}:   {len(t.df):,} rows   "
                              f"x   {len(t.df.columns)} columns")
        except AskError:
            shape_var.set("shape: keep typing... (preview needs valid steps above)")
        except Exception:
            shape_var.set("shape: -")

    # --- file dialogs (E2/E7) ---
    def open_program(_e=None):
        path = filedialog.askopenfilename(
            filetypes=[("Ask programs", "*.ask"), ("All files", "*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            set_messages(f"Couldn't open {path}: {e}", error=True)
            return
        editor.delete("1.0", "end")
        editor.insert("1.0", text)
        state["program_path"] = path
        schedule_preview()

    def save_program(_e=None):
        path = state["program_path"] or filedialog.asksaveasfilename(
            defaultextension=".ask", filetypes=[("Ask programs", "*.ask")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(editor.get("1.0", "end-1c"))
            state["program_path"] = path
            set_messages(f"Saved program to {path}.")
        except OSError as e:
            set_messages(f"Couldn't save: {e}", error=True)

    def _fmt_size(n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def refresh_files_list():
        files_list.delete(*files_list.get_children())
        for name in list_uploads():
            full = os.path.join(UPLOADS_DIR, name)
            try:
                st = os.stat(full)
                size = _fmt_size(st.st_size)
                import datetime
                mtime = datetime.datetime.fromtimestamp(
                    st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                size, mtime = "", ""
            files_list.insert("", "end", iid=name, values=(name, size, mtime))

    def insert_load_line(name):
        editor.insert("insert", f'load "{name}"\n')
        schedule_preview()

    def on_file_double_click(_e=None):
        sel = files_list.selection()
        if sel:
            insert_load_line(sel[0])

    def on_file_right_click(event):
        sel = files_list.identify_row(event.y)
        if not sel:
            return
        files_list.selection_set(sel)
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label=f'Insert: load "{sel}"',
                         command=lambda: insert_load_line(sel))
        menu.add_separator()
        menu.add_command(label="Remove from uploads",
                         command=lambda: remove_file(sel))
        menu.tk_popup(event.x_root, event.y_root)

    def remove_file(name):
        try:
            os.remove(os.path.join(UPLOADS_DIR, name))
        except OSError as e:
            set_messages(f"Couldn't remove {name}: {e}", error=True)
        refresh_files_list()

    files_list.bind("<Double-Button-1>", on_file_double_click)
    files_list.bind("<Button-2>", on_file_right_click)   # macOS right-click
    files_list.bind("<Button-3>", on_file_right_click)   # other platforms

    def open_data(_e=None):
        paths = filedialog.askopenfilenames(
            filetypes=[("Data files", "*.csv *.tsv *.xlsx *.xls *.json"),
                       ("All files", "*")])
        if not paths:
            return
        names = []
        for p in paths:
            try:
                names.append(register_upload(p))
            except OSError as e:
                set_messages(f"Couldn't upload {p}: {e}", error=True)
                return
        refresh_files_list()
        if len(names) == 1:
            insert_load_line(names[0])
            set_messages(f"Uploaded {names[0]}.")
        else:
            insert_load_line(names[0])
            rest = ", ".join(names[1:])
            editor.insert("insert",
                         f"# also uploaded: {rest}\n"
                         f"# see them anytime in the Files tab, or reference "
                         f"with combine/stack, e.g.: combine with "
                         f"{os.path.splitext(names[1])[0]} on <key>\n")
            schedule_preview()
            set_messages(f"Uploaded {len(names)} files: "
                         f"{', '.join(names)}. See the Files tab.")

    def export_table(_e=None):
        table = state["table"]
        if table is None:
            set_messages("Run a program first - there's no table to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Excel", "*.xlsx")])
        if not path:
            return
        try:
            if path.lower().endswith(".xlsx"):
                try:
                    table.df.to_excel(path, index=False)
                except ImportError:
                    set_messages("Exporting .xlsx needs the 'openpyxl' package "
                                 "(pip install openpyxl). Saved nothing.",
                                 error=True)
                    return
            else:
                table.df.to_csv(path, index=False)
            set_messages(f"Exported {len(table.df):,} rows to {path}.")
        except OSError as e:
            set_messages(f"Couldn't export: {e}", error=True)

    def save_chart(_e=None):
        chart = state["chart"]
        if chart is None:
            set_messages("Run a program with a chart step first.")
            return
        animated = (chart.kind == "bubble"
                   and any(m.kind == "animate" for m in chart.mods))
        filetypes = [("PNG image", "*.png"), ("SVG vector", "*.svg"),
                    ("PDF", "*.pdf")]
        if animated:
            filetypes = [("Animated GIF", "*.gif")] + filetypes
        path = filedialog.asksaveasfilename(
            defaultextension=".gif" if animated else ".png",
            filetypes=filetypes)
        if not path:
            return
        try:
            if animated and path.lower().endswith((".gif", ".mp4")):
                set_messages("Rendering the animation, this can take a few "
                             "seconds…")
                root.update_idletasks()
                save_chart_animation(chart, path)
            else:
                save_chart_image(chart, path, dpi=200)
            set_messages(f"Saved the chart to {path}.")
        except Exception as e:
            set_messages(f"Couldn't save the chart: {e}", error=True)

    def on_sample(event=None):
        name = sample_var.get()
        if name in SAMPLES:
            editor.delete("1.0", "end")
            editor.insert("1.0", SAMPLES[name])
            state["program_path"] = None
            highlight()
            update_gutter()
            on_run()

    run_btn.bind("<Button-1>", on_run)
    open_btn.bind("<Button-1>", open_program)
    save_prog_btn.bind("<Button-1>", save_program)
    open_data_btn.bind("<Button-1>", open_data)
    export_table_btn.bind("<Button-1>", export_table)
    save_chart_btn.bind("<Button-1>", save_chart)
    sample_menu.bind("<<ComboboxSelected>>", on_sample)
    editor.bind("<KeyRelease>", schedule_preview)
    editor.bind("<ButtonRelease-1>", schedule_preview)
    # return "break" so Cmd/Ctrl+Enter doesn't ALSO insert a newline (E6)
    editor.bind("<Control-Return>", on_run)
    editor.bind("<Command-Return>", on_run)
    root.bind("<Control-Return>", on_run)
    root.bind("<Command-Return>", on_run)

    fill_steps([])
    refresh_files_list()
    set_messages("Ready. Press Run (or Cmd/Ctrl+Enter). Pick a sample to see Ask "
                 "in action. The Steps tab explains each line; Show code reveals "
                 "the pandas/R behind it.")
    highlight()
    update_gutter()
    update_shape()
    root.mainloop()


def _darken(hexcolor):
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{int(r * .8):02X}{int(g * .8):02X}{int(b * .8):02X}"


def fmt_cell(value, coltype):
    """Format one table cell for display (E4: numpy scalars, bools, dates)."""
    if value is None or value is pd.NA:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "true" if value else "false"
    if isinstance(value, (float, np.floating)):
        v = float(value)
        if math.isnan(v):
            return ""
        if math.isinf(v):
            return "∞" if v > 0 else "-∞"
        if coltype == "Money":
            return f"${v:,.2f}"
        if v == int(v) and abs(v) < 1e15:
            return f"{int(v):,}"
        return f"{v:,.2f}"
    if isinstance(value, (int, np.integer)):
        v = int(value)
        return f"${v:,.2f}" if coltype == "Money" else f"{v:,}"
    if isinstance(value, pd.Timestamp):
        if value.hour == value.minute == value.second == 0:
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)
