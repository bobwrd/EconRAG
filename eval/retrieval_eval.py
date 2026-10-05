"""
Retrieval-only eval: does the chunk containing each question's `evidence`
phrase (eval/questions.json) make it into the candidate pool? No LLM, so a
run takes seconds — use it to measure every extraction/retrieval change.

    .venv/bin/python eval/retrieval_eval.py            # hybrid retrieval only
    .venv/bin/python eval/retrieval_eval.py --rerank   # + Laya top-5 (~3 min)
    .venv/bin/python eval/retrieval_eval.py --reworded # paraphrased questions
    .venv/bin/python eval/retrieval_eval.py --model BAAI/bge-small-en-v1.5
        # try another embedding model without touching data/: chunk embeddings
        # are computed once and cached in data/embeddings_<model>.npy

eval/reworded_questions.json asks the same things as questions.json in everyday
words that avoid the source's terms ("fall behind on loans", not
"delinquency"): that's where keyword search can't help and the embedding model
has to carry retrieval.

Evidence matching ignores whitespace, quotes, and dash style, so glued words
("73%ofBlack") still count as present, but text scrambled by false table
detection ("V-sh | aped") does not — that text is unreadable to the models too.
"""

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ask  # noqa: E402


def normalize(text: str) -> str:
    text = re.sub(r"[‐-―−]", "-", text.lower())
    return re.sub(r"[\s\"'“”‘’`]", "", text)


def evaluate(questions, chunks, rank_fn, rerank_fn=None):
    """rank_fn(question) -> chunk indices, best first (whole corpus).
    rerank_fn(question, top15_indices) -> top-5 indices."""
    normalized = [normalize(c["text"]) for c in chunks]
    rows = []
    for q in questions:
        needle = normalize(q["evidence"])
        holders = {i for i, text in enumerate(normalized) if needle in text}
        order = list(rank_fn(q["question"]))
        rank = next((r + 1 for r, i in enumerate(order) if i in holders), None)
        row = {"id": q["id"], "in_corpus": len(holders), "rank": rank}
        if rerank_fn is not None:
            row["after_rerank"] = any(i in holders for i in rerank_fn(q["question"], order[:ask.CANDIDATE_POOL]))
        rows.append(row)
    return rows


def summarize(rows, label=""):
    n = len(rows)
    ranks = [r["rank"] for r in rows]
    found = sum(r["in_corpus"] > 0 for r in rows)
    at5 = sum(1 for k in ranks if k and k <= 5)
    at15 = sum(1 for k in ranks if k and k <= ask.CANDIDATE_POOL)
    mrr = sum(1 / k for k in ranks if k) / n
    line = (f"{label:28s} evidence in corpus {found}/{n} | top-5 {at5}/{n} | "
            f"top-15 {at15}/{n} | MRR {mrr:.3f}")
    if "after_rerank" in rows[0]:
        line += f" | after Laya top-5 {sum(r['after_rerank'] for r in rows)}/{n}"
    print(line)
    return {"in_corpus": found, "top5": at5, "top15": at15, "mrr": mrr}


def main():
    import numpy as np

    source = "reworded_questions.json" if "--reworded" in sys.argv else "questions.json"
    questions = [q for q in json.loads((ROOT / "eval" / source).read_text())
                 if q.get("evidence") and q["id"] != "both_unemp_migration"]
    name = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else ask.EMBEDDING_MODEL
    chunks, embeddings, bm25 = ask.load_index()
    model = ask.load_embedder(name)
    if name != ask.EMBEDDING_MODEL:
        cache = ROOT / "data" / f"embeddings_{name.replace('/', '_')}.npy"
        if not cache.exists():
            np.save(cache, model.encode([c["text"] for c in chunks], show_progress_bar=True,
                                        convert_to_numpy=True))
        embeddings = np.load(cache)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    print(f"{name} (reads {model.max_seq_length} tokens/chunk), {source}")
    position = {id(c): i for i, c in enumerate(chunks)}

    def rank_fn(question):  # the pipeline's own retrieval, over the whole corpus
        ranked = ask.top_k_chunks(question, model, chunks, embeddings, bm25, k=len(chunks))
        return [position[id(c)] for c, _ in ranked]

    rerank_fn = None
    if "--rerank" in sys.argv:
        import laya
        agent = laya.load(ask.LAYA_MODEL, device="cpu")

        def rerank_fn(question, top):
            kept = ask.rerank_with_laya(question, [(chunks[i], 0.0) for i in top], agent)
            ids = {id(c) for c, _ in kept}
            return [i for i in top if id(chunks[i]) in ids]

    rows = evaluate(questions, chunks, rank_fn, rerank_fn)
    for r in rows:
        status = "NOT IN CORPUS" if not r["in_corpus"] else f"rank {r['rank']}"
        extra = "" if "after_rerank" not in r else ("  kept by Laya" if r["after_rerank"] else "  dropped by Laya")
        print(f"  {r['id']:22s} {status}{extra}")
    summarize(rows, "pipeline")


if __name__ == "__main__":
    main()
