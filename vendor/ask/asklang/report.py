"""Self-contained HTML report: step trace + final table + chart (base64 PNG)."""

import base64
import io
from html import escape as _html_escape

_REPORT_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a;line-height:1.5}
h1{font-size:1.7rem}h2{margin-top:2rem;border-bottom:1px solid #e5ddce;padding-bottom:.3rem}
ol{padding-left:1.2rem}li{margin:.25rem 0}
img{max-width:100%;border:1px solid #eee;border-radius:8px;margin:1rem 0}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin-top:1rem}
th,td{border:1px solid #e5ddce;padding:.35rem .55rem;text-align:left}
th{background:#f3efe7}
pre{background:#faf7f0;padding:1rem;border-radius:8px;overflow:auto;font-size:.85rem}
footer{margin-top:3rem;color:#999;font-size:.85rem}
"""


def build_html_report(src, trace, table, chart):
    """A single self-contained HTML file: step trace + final table + chart.
    No external assets - the chart is embedded as a base64 PNG."""
    chart_html = ""
    if chart is not None:
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg

            from .charts import render_chart
            fig = render_chart(chart)
            FigureCanvasAgg(fig)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            chart_html = f'<img alt="chart" src="data:image/png;base64,{b64}">'
        except Exception:
            chart_html = "<p>(the chart could not be drawn)</p>"
    steps = "\n".join(f"<li>{_html_escape(s.text)}</li>" for s in trace) \
        or "<li>(no steps ran)</li>"
    table_html = ""
    if table is not None:
        table_html = table.df.head(200).to_html(index=False, border=0, na_rep="")
        if len(table.df) > 200:
            table_html += f"<p>(showing 200 of {len(table.df):,} rows)</p>"
    program = _html_escape(src.strip())
    return (
        "<!doctype html>\n<html><head><meta charset='utf-8'>"
        "<title>Ask report</title><style>" + _REPORT_CSS + "</style></head><body>"
        "<h1>Ask report</h1>"
        "<h2>What this program did, step by step</h2><ol>" + steps + "</ol>"
        "<h2>Result</h2>" + chart_html + table_html +
        "<h2>The Ask program</h2><pre>" + program + "</pre>"
        "<footer>Made with Ask.</footer></body></html>")
