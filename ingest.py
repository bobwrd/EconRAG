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
# (Assumes single-column pages, true of every paper here: a two-column
# layout would join the columns' lines with " | ".)
WORD_X_TOLERANCE = 1.5
COLUMN_GAP_RATIO = 1.0  # gap wider than this x font size = column break


def page_lines(page) -> list[str]:
    # Rotated text (figure axis labels) extracts reversed ("stneraP") — drop it.
    page = page.filter(lambda obj: obj.get("object_type") != "char" or obj.get("upright", True))
    words = page.extract_words(x_tolerance=WORD_X_TOLERANCE, extra_attrs=["size"])
    words.sort(key=lambda w: (w["top"], w["x0"]))
    rows, row = [], []
    for w in words:
        if row and abs(w["top"] - row[0]["top"]) > 0.5 * max(row[0]["size"], 1):
            rows.append(row)
            row = []
        row.append(w)
    if row:
        rows.append(row)
    lines = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        parts = [row[0]["text"]]
        for prev, w in zip(row, row[1:]):
            gap = w["x0"] - prev["x1"]
            parts.append((" | " if gap > COLUMN_GAP_RATIO * w["size"] else " ") + w["text"])
        lines.append("".join(parts))
    return lines


def extract_text(pdf_path: Path) -> str:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
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
