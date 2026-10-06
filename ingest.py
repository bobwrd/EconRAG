"""
Reads every PDF in docs/, splits it into overlapping chunks, embeds each
chunk, and saves the result to data/. Run this once, then again whenever
you add or change a PDF.
"""

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pdfplumber
from sentence_transformers import SentenceTransformer

DOCS_DIR = Path("docs")
DATA_DIR = Path("data")
CHUNK_WORDS = 500
CHUNK_OVERLAP = 50
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # see NOTES.md "Retrieval"
# pdfplumber is CPU-bound and single-threaded; a few processes extract PDFs in
# parallel. Capped low because each worker holds a whole parsed PDF in memory.
EXTRACT_WORKERS = 4


# Two pdfplumber defaults wrecked the extracted text (see NOTES.md gotcha #4):
# - x_tolerance=3 is too loose for tightly set papers: whole sentences came out
#   glued ("Weshowthatintergenerationalmobility...") — 6-8% of the "words" in
#   the Opportunity Insights papers. 1.5 splits them correctly.
# - find_tables(strategy "text") treated most prose as tables (584 of 654
#   detected "tables" were <20% numeric) and cut words into cells
#   ("V-sh | aped"), making whole abstracts unreadable and unsearchable.
# Instead every page is laid out line by line from word positions, with
# " | " only where the horizontal gap between words exceeds about one
# character height. Prose has no such gaps, so it comes out as clean lines;
# table columns do, so rows come out as "Label | 45.4 | 45.1 | -0.7".
# Two-column pages (Science, PNAS, some reports) would come out with both
# columns' lines joined by " | ", so `two_column_split` looks for a vertical
# gutter near the middle and lays out the left column, then the right; rows
# that cross the gutter (titles, full-width figures) stay whole, in order.
WORD_X_TOLERANCE = 1.5
COLUMN_GAP_RATIO = 1.0  # gap wider than this x font size = column break
# A gutter counts only if, on rows with text on both sides, each side averages
# at least this many words: prose columns do, table columns ("45.4 | 45.1") don't.
MIN_COLUMN_WORDS = 4
MIN_RUN_WORDS = 3
MIN_TWO_SIDED_SHARE = 0.4  # of the page's rows


def layout_rows(words: list[dict]) -> list[list[dict]]:
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows, row = [], []
    for w in words:
        if row and abs(w["top"] - row[0]["top"]) > 0.5 * max(row[0]["size"], 1):
            rows.append(row)
            row = []
        row.append(w)
    if row:
        rows.append(row)
    return rows


def row_lines(rows: list[list[dict]]) -> list[str]:
    lines = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        parts = [row[0]["text"]]
        for prev, w in zip(row, row[1:]):
            gap = w["x0"] - prev["x1"]
            parts.append((" | " if gap > COLUMN_GAP_RATIO * w["size"] else " ") + w["text"])
        lines.append("".join(parts))
    return lines


def _spans(row: list[dict]) -> list[tuple[float, float]]:
    """x extents of the row's runs of words joined by ordinary spaces (wide gaps split runs)."""
    row = sorted(row, key=lambda w: w["x0"])
    spans, (x0, x1) = [], (row[0]["x0"], row[0]["x1"])
    for prev, w in zip(row, row[1:]):
        if w["x0"] - prev["x1"] > COLUMN_GAP_RATIO * w["size"]:
            spans.append((x0, x1))
            x0 = w["x0"]
        x1 = max(x1, w["x1"])
    spans.append((x0, x1))
    return spans


def _splits_at(row: list[dict], gutter: float) -> bool:
    """True if the row breaks at the gutter with a wide gap and every run of
    words is prose-like (MIN_RUN_WORDS+); table rows break into short cells."""
    runs = _spans(row)
    if not any(a[1] <= gutter <= b[0] for a, b in zip(runs, runs[1:])):
        return False
    words = [sum(x0 <= w["x0"] and w["x1"] <= x1 for w in row) for x0, x1 in runs]
    return min(words) >= MIN_RUN_WORDS


def _full_width(row: list[dict], gutter: float) -> bool:
    """A run of words crosses the gutter: a title or other full-width line."""
    return any(x0 < gutter < x1 for x0, x1 in _spans(row))


def two_column_split(rows: list[list[dict]], left: float, right: float) -> float | None:
    """x of a gutter if the region [left, right] holds side-by-side prose columns, else None."""
    if len(rows) < 10:
        return None
    width = right - left
    lo, hi = int(left + 0.3 * width), int(left + 0.7 * width)
    crossings = np.zeros(hi - lo)
    for row in rows:
        covered = np.zeros(hi - lo, dtype=bool)  # only wide gaps can be a gutter
        for x0, x1 in _spans(row):
            covered[max(int(x0) - lo, 0):max(int(x1) + 1 - lo, 0)] = True
        crossings += covered
    gutter = lo + int(np.argmin(crossings + 0.001 * np.abs(np.arange(lo, hi) - (left + right) / 2)))
    two_sided = [(sum(w["x1"] <= gutter for w in row), sum(w["x0"] >= gutter for w in row)) for row in rows
                 if _splits_at(row, gutter)]
    if len(two_sided) < MIN_TWO_SIDED_SHARE * len(rows):
        return None
    if min(np.mean([s[0] for s in two_sided]), np.mean([s[1] for s in two_sided])) < MIN_COLUMN_WORDS:
        return None
    return gutter




def page_lines(page) -> list[str]:
    # Rotated text (figure axis labels) extracts reversed ("stneraP") — drop it.
    # (Not dedupe_chars: it fixes headings printed twice for fake bold, "CCOOSSTT",
    # but also merges real double letters in tight fonts: "Dallas" -> "Dalas".)
    page = page.filter(lambda obj: obj.get("object_type") != "char" or obj.get("upright", True))
    words = page.extract_words(x_tolerance=WORD_X_TOLERANCE, extra_attrs=["size"])
    return region_lines(words, 0.0, float(page.width))


def region_lines(words: list[dict], left: float, right: float, depth: int = 0) -> list[str]:
    """Lines of a page region, reading side-by-side columns one after another
    (recursively, so three columns or a two-page spread work too)."""
    rows = layout_rows(words)
    gutter = two_column_split(rows, left, right) if depth < 3 and words else None
    if gutter is None:
        return row_lines(rows)
    lines, section = [], []
    for row in rows + [None]:
        if row is None or _full_width(row, gutter):
            if section:
                lines += region_lines([w for w in section if w["x1"] <= gutter], left, gutter, depth + 1)
                lines += region_lines([w for w in section if w["x1"] > gutter], gutter, right, depth + 1)
                section = []
            if row:
                lines += row_lines([row])
        else:
            section += row
    return lines


def open_pdf(pdf_path: Path):
    """pdfplumber.open, repairing pages that lack a MediaBox (one NBER PDF does)."""
    pdf = pdfplumber.open(pdf_path)
    try:
        pdf.pages
        return pdf
    except TypeError:
        try:
            pdf.close()
        except TypeError:  # close() walks the same broken pages
            pdf.stream.close()
    import io
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, RectangleObject
    writer = PdfWriter()
    for p in PdfReader(pdf_path).pages:
        if "/MediaBox" not in p:
            p[NameObject("/MediaBox")] = RectangleObject([0, 0, 612, 792])  # US Letter
        writer.add_page(p)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return pdfplumber.open(buf)


def extract_text(pdf_path: Path) -> str:
    lines = []
    with open_pdf(pdf_path) as pdf:
        for page in pdf.pages:
            lines.extend(page_lines(page))
            # pdfplumber caches every parsed object per page; drop it so a
            # long paper doesn't accumulate all its pages in memory at once.
            page.flush_cache()
    return "\n".join(lines)


def chunk_text(text: str, source: str) -> list[dict]:
    """~CHUNK_WORDS-word chunks with ~CHUNK_OVERLAP words of overlap, split on
    line boundaries so table rows stay on their own lines."""
    lines = [line for line in text.split("\n") if line.strip()]
    sizes = [len(line.split()) for line in lines]
    chunks, start = [], 0
    while start < len(lines):
        end, words = start, 0
        while end < len(lines) and (words < CHUNK_WORDS or end == start):
            words += sizes[end]
            end += 1
        chunks.append({"source": source, "text": "\n".join(lines[start:end])})
        if end >= len(lines):
            break
        back, overlap = end, 0
        while back > start + 1 and overlap < CHUNK_OVERLAP:
            back -= 1
            overlap += sizes[back]
        start = back
    return chunks


def main():
    pdf_paths = sorted(DOCS_DIR.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found in {DOCS_DIR}/ — add some and re-run.")
        return

    print(f"Found {len(pdf_paths)} PDF(s): {[p.name for p in pdf_paths]}")

    all_chunks = []
    with ProcessPoolExecutor(max_workers=min(EXTRACT_WORKERS, len(pdf_paths))) as pool:
        for pdf_path, text in zip(pdf_paths, pool.map(extract_text, pdf_paths)):
            doc_chunks = chunk_text(text, source=pdf_path.name)
            print(f"  {pdf_path.name}: {len(text.split())} words -> {len(doc_chunks)} chunks")
            all_chunks.extend(doc_chunks)

    print(f"\nTotal chunks: {len(all_chunks)}")
    print(f"Loading embedding model ({EMBEDDING_MODEL})...")
    model = SentenceTransformer(EMBEDDING_MODEL)

    # Note: the model truncates at 256 tokens, so each ~720-token chunk is
    # embedded from roughly its first third. Embedding overlapping windows and
    # scoring a chunk by its best/mean window was tried and measured no better
    # (slightly worse on some queries), so whole-chunk embedding stays.
    print("Embedding chunks...")
    texts = [c["text"] for c in all_chunks]
    embeddings = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)

    DATA_DIR.mkdir(exist_ok=True)
    # one file per model — must match ask.embeddings_path()
    np.save(DATA_DIR / f"embeddings_{EMBEDDING_MODEL.replace('/', '_')}.npy", embeddings)
    with open(DATA_DIR / "chunks.json", "w") as f:
        json.dump(all_chunks, f)

    print(f"\nSaved {embeddings.shape[0]} embeddings (dim={embeddings.shape[1]}) to {DATA_DIR}/")


if __name__ == "__main__":
    main()
