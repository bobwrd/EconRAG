"""
Compares generation models on eval/questions.json.

    .venv/bin/python eval/run_eval.py phi3.5@4096 phi4-mini@8192 groq@16384

`groq@N` uses ask.GROQ_MODEL via Groq's API (needs GROQ_API_KEY), with no
fallback to the local model — a failed request fails the run. On free-tier
rate limits it waits as long as Groq's retry-after header asks, then retries.

Routing, retrieval, and Laya reranking run once per question and are shared
by every model, so only generation differs between configs. The route is
taken from the question file (this tests generation, not the router; Laya's
own routing decision is recorded alongside and scored separately).

Scoring is automatic and deliberately literal — read eval/results/*.json too:
  - doc questions: every `must` regex appears in the answer (case-insensitive)
  - live questions: the current FRED value appears, with no "I can't access
    real-time data" refusal
  - fabricated refs: "Table N" / "Figure N" in an answer that isn't in the
    context the model was given
"""

import contextlib
import io
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.chdir(ROOT)  # ask.py resolves data/ relative to the project root

import laya  # noqa: E402
import requests  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

import ask  # noqa: E402
import fred  # noqa: E402

EVAL_DIR = ROOT / "eval"
RESULTS_DIR = EVAL_DIR / "results"
REFUSAL = re.compile(r"real[- ]time|(don't|do not|cannot|can't) (have )?access", re.I)
REF = re.compile(r"\b(?:Table|Figure|Fig\.)\s*[A-Z]?\d+[A-Za-z]?", re.I)


def fred_value_pattern(summary: str, key: str) -> str:
    """Regex accepting the live value as written by FRED or rounded (3.35302 -> 3.35302|3.35|3.4)."""
    line = next(l for l in summary.splitlines() if key in l)
    value = re.search(r":\s*([\d.]+)", line).group(1)
    forms = {value}
    if "." in value:
        number = float(value)
        forms |= {f"{number:.2f}".rstrip("0").rstrip("."), f"{number:.1f}".rstrip("0").rstrip(".")}
    return "|".join(rf"(?<![\d.]){re.escape(f)}(?!\d)" for f in sorted(forms, key=len, reverse=True))


def check(answer: str, patterns: list[str]) -> list[str]:
    return [p for p in patterns if not re.search(p, answer, re.I)]


def fabricated_refs(answer: str, context: str) -> list[str]:
    return [r for r in set(REF.findall(answer))
            if re.sub(r"\s+", "", r.lower()) not in re.sub(r"\s+", "", context.lower())]


def retrieve_all(questions):
    """Laya routing + retrieval + reranking for every question, done once."""
    chunks, embeddings, bm25 = ask.load_index()
    model = SentenceTransformer(ask.EMBEDDING_MODEL)
    agent = laya.load(ask.LAYA_MODEL, device="cpu")
    out = {}
    for q in questions:
        laya_route = ask.route_query(q["question"], agent)
        results = None
        if q["route"] in ("documents", "both"):
            candidates = ask.top_k_chunks(q["question"], model, chunks, embeddings, bm25)
            results = ask.rerank_with_laya(q["question"], candidates, agent)
        out[q["id"]] = {"laya_route": laya_route, "results": results}
        print(f"  retrieved {q['id']} (Laya routed: {laya_route})", flush=True)
    return out


def generate(question, context, tokenizer, groq=False):
    buf = io.StringIO()
    for attempt in range(5):
        start = time.time()
        try:
            with contextlib.redirect_stdout(buf):
                answer = (ask.ask_groq(question, context) if groq
                          else ask.ask_llm(question, context, tokenizer))
            break
        except ask.GroqUnavailable as e:
            if e.retry_after is None or attempt == 4:
                raise
            time.sleep(e.retry_after)
    return answer, time.time() - start, "WARNING: prompt filled num_ctx" in buf.getvalue()


def run_config(config, questions, retrieved, fred_summary):
    model_name, num_ctx = config.split("@")
    groq = model_name == "groq"
    ask.NUM_CTX = int(num_ctx)  # for groq, just the budget build_doc_context packs into
    if not groq:
        ask.OLLAMA_MODEL = model_name
    tokenizer = AutoTokenizer.from_pretrained(ask.MODELS["phi3.5" if groq else model_name]["tokenizer"])
    # load the model before timing anything, as ask.py would have after one question
    if not groq:
        requests.post(ask.OLLAMA_URL, json={"model": model_name, "prompt": "hi", "keep_alive": "5m",
                                            "options": {"num_ctx": ask.NUM_CTX, "num_predict": 1}})
    rows = []
    for q in questions:
        row = {"id": q["id"], "route": q["route"], "laya_route": retrieved[q["id"]]["laya_route"],
               "answers": {}, "missing": [], "fabricated": [], "seconds": 0.0, "truncated": False}
        if q["route"] in ("live_data", "both"):
            context = f"Live economic indicators (from FRED):\n{fred_summary}"
            answer, secs, trunc = generate(q["question"], context, tokenizer, groq)
            row["answers"]["live"] = answer
            row["seconds"] += secs
            row["truncated"] |= trunc
            row["missing"] += [f"FRED {q['fred']}"] if check(answer, [fred_value_pattern(fred_summary, q["fred"])]) else []
            row["missing"] += ["(refused: claimed no real-time access)"] if REFUSAL.search(answer) else []
        if q["route"] in ("documents", "both"):
            context = ask.build_doc_context(q["question"], retrieved[q["id"]]["results"], tokenizer)
            answer, secs, trunc = generate(q["question"], context, tokenizer, groq)
            row["answers"]["documents"] = answer
            row["seconds"] += secs
            row["truncated"] |= trunc
            row["missing"] += check(answer, q["must"])
            row["fabricated"] = fabricated_refs(answer, context)
            row["chunks_in_context"] = context.count("(from ")
        row["passed"] = not row["missing"]
        rows.append(row)
        status = "PASS" if row["passed"] else "FAIL " + "; ".join(row["missing"])
        print(f"  [{config}] {q['id']:22s} {row['seconds']:5.1f}s  {status}"
              + (f"  FABRICATED {row['fabricated']}" if row["fabricated"] else ""), flush=True)
    if not groq:
        requests.post(ask.OLLAMA_URL, json={"model": model_name, "keep_alive": 0})
    return rows


def main():
    configs = sys.argv[1:] or [f"{name}@{cfg['num_ctx']}" for name, cfg in ask.MODELS.items()]
    questions = json.loads((EVAL_DIR / "questions.json").read_text())
    print(f"Retrieving for {len(questions)} questions...", flush=True)
    retrieved = retrieve_all(questions)
    fred_summary = fred.get_indicator_summary()
    RESULTS_DIR.mkdir(exist_ok=True)
    misrouted = [q["id"] for q in questions if retrieved[q["id"]]["laya_route"] != q["route"]]
    print(f"\nRouting: {len(questions) - len(misrouted)}/{len(questions)} correct"
          + (f" (misrouted: {', '.join(misrouted)})" if misrouted else ""), flush=True)

    summary = []
    for config in configs:
        print(f"\n=== {config}", flush=True)
        rows = run_config(config, questions, retrieved, fred_summary)
        (RESULTS_DIR / f"{config.replace('@', '_')}.json").write_text(json.dumps(rows, indent=2))
        n = len(rows)
        summary.append((config, sum(r["passed"] for r in rows), n,
                        sum(len(r["fabricated"]) for r in rows),
                        sum(r["truncated"] for r in rows),
                        sum(r["seconds"] for r in rows) / n))

    print(f"\n{'config':18s} {'passed':>8s} {'fabricated refs':>16s} {'truncated':>10s} {'avg s/question':>15s}")
    for config, passed, n, fab, trunc, secs in summary:
        print(f"{config:18s} {passed:>4d}/{n:<3d} {fab:>16d} {trunc:>10d} {secs:>15.1f}")


if __name__ == "__main__":
    main()
