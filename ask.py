"""
Answers a question from the chunks/embeddings built by ingest.py and/or live
FRED data: Laya routes the question -> cosine similarity candidates -> Laya
reranks -> an LLM writes the answer.

With a GROQ_API_KEY and a network, questions go to analyst.py instead: Groq's
model calls tools (paper search, FRED, Opportunity Atlas county data) and
writes the answer from their results. The routed local pipeline above is the
fallback when Groq is unreachable, and the only path without a key.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import laya
import numpy as np
import requests
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

import fred
from analyst import Analyst, paper_label
from bm25 import BM25
from atlas import Atlas
from worldbank import WorldBank
from groq_client import GROQ_API_KEY, GROQ_MODEL, GroqUnavailable
from groq_client import post as groq_post

DATA_DIR = Path("data")
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # see NOTES.md "Retrieval"
# Text some embedding models expect before a search query (not before passages).
QUERY_PREFIXES = {"BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: "}
LAYA_MODEL = "convaiinnovations/laya"
# Generation models this pipeline knows how to budget for. `tokenizer` is the
# model's own HF tokenizer (~2MB, no weights) used to measure prompts exactly —
# each verified token-for-token against Ollama's counts. `num_ctx` is sized to
# the model's KV cache: phi3.5 has no grouped-query attention (~384KB/token,
# 1.5GB at 4096); phi4-mini does (~128KB/token, 1GB at 8192), so it fits all
# five chunks in less memory than phi3.5 uses for ~four.
MODELS = {
    "phi3.5": {"tokenizer": "microsoft/Phi-3.5-mini-instruct", "num_ctx": 4096},
    "phi4-mini": {"tokenizer": "microsoft/Phi-4-mini-instruct", "num_ctx": 8192},
}
OLLAMA_MODEL = "phi3.5"
OLLAMA_TOKENIZER = MODELS[OLLAMA_MODEL]["tokenizer"]
OLLAMA_URL = "http://localhost:11434/api/generate"
# Groq (free tier) answers when a key is set and it's reachable — see
# analyst.py; any failure falls back to the local pipeline for that question.
USE_GROQ = bool(GROQ_API_KEY)
GROQ_MAX_TOKENS = 4096  # gpt-oss's hidden reasoning counts against this too
CANDIDATE_POOL = 15  # chunks pulled by cosine similarity, before reranking
TOP_K = 5            # chunks kept after Laya reranks the pool
# Reciprocal-rank-fusion constant for retrieval rank + Laya rank. At the usual
# 60, ranks 1 and 15 score almost alike (1/61 vs 1/75), so neither ranking can
# promote its best picks; 10 lets them. Evidence in the top 3 / top 5 after
# reranking: original questions 11->13 / 14->16 of 18, reworded 4->5 / 5->6
# of 12; c=5 scored the same, c=20 in between (NOTES.md "Reranking").
RERANK_RRF_C = 10

# Ollama's context window is shared by the prompt AND the answer. Five chunks
# average ~4,000 phi3.5 tokens, so sending them all overflowed num_ctx: Ollama
# silently cut the START of the prompt (the instructions and the top-ranked
# chunk), then ran out of room mid-answer and "context shifted", rambling for
# thousands of tokens. Now the context is packed to fit, best chunk first.
NUM_CTX = MODELS[OLLAMA_MODEL]["num_ctx"]  # see NOTES.md gotcha #2 before raising
# Paper passages per analyst search: ~1,800 tokens (2-3 chunks), not all five
# — Groq's free tier allows 8,000 tokens/minute, a single request over that
# fails outright, and every tool round re-sends the passages.
AGENT_PAPERS_CTX = 3000
ANSWER_RESERVE = 768   # tokens guaranteed for the answer (typical: 200-500)
CHAT_TEMPLATE_TOKENS = 16  # chat-template wrapper tokens (measured: phi3.5 9, phi4-mini 3)
# phi3.5 stays loaded between questions (Ollama's default) and is unloaded on
# exit. Unloading it after every answer was measured too: Laya got faster, but
# phi3.5 then generated 2-4x slower after each cold reload — net slower.
KEEP_ALIVE = "5m"

RELEVANCE_QUESTION = {
    "relevance": {
        "type": "score",
        "instructions": "How relevant is this passage to answering the query in the state?",
        "criteria": ["not relevant", "somewhat relevant", "relevant", "highly relevant"],
    }
}

ROUTE_QUESTIONS = {
    "needs_live_data": {
        "type": "noul",
        "instructions": "Does answering this question require reporting a current numeric "
                        "economic statistic, such as the inflation rate, unemployment rate, "
                        "GDP, or an interest rate?",
    },
    "needs_documents": {
        "type": "noul",
        "instructions": "Does answering this question require explanation, findings, or "
                        "analysis from academic economics research papers, such as about "
                        "mobility, credit access, migration, discrimination, or causal "
                        "mechanisms?",
    },
}

PROMPT_TEMPLATE = """You are an economics assistant. Answer the question using ONLY the context below. Cite specific numbers, percentages, and figures from the context wherever they're available — a vague summary is not acceptable if concrete data is present. If the context truly doesn't contain any information relevant to the question, say so explicitly rather than guessing.

Never invent a table number, figure number, or exact paper title. If you don't know the precise title or table number of a source, describe its finding in your own words instead — do not present a made-up citation as if it were real. Fabricating a specific-sounding reference is worse than not citing one at all.

If the "Live economic indicators" section below lists a value relevant to the question, you MUST explicitly state that exact value somewhere in your answer, even if the rest of the answer focuses on discussing research findings. That section was fetched from the FRED API just now and inserted directly into this prompt — you are reading it, not recalling it from memory, so your training cutoff is irrelevant to it. Responding with "I can't provide real-time data" when the value is listed right below is INCORRECT, not cautious.

Context:
{context}

Question: {question}

Answer:"""


_ollama = requests.Session()  # one pooled connection to Ollama, reused per question


def embeddings_path(model_name: str = EMBEDDING_MODEL) -> Path:
    """One file per embedding model, so switching models (or checking out an
    older commit that uses another one) never pairs a query encoder with
    another model's chunk vectors. Same naming in ingest.py."""
    return DATA_DIR / f"embeddings_{model_name.replace('/', '_')}.npy"


def load_index(model_name: str = EMBEDDING_MODEL):
    """The paper library built by ingest.py; empty (searches switched off) until it's built."""
    if not (DATA_DIR / "chunks.json").exists() or not embeddings_path(model_name).exists():
        print("  No paper library yet (run ingest.py after adding PDFs to docs/): paper search is off.")
        return [], None, None
    chunks = json.load(open(DATA_DIR / "chunks.json"))
    embeddings = np.load(embeddings_path(model_name))
    # Normalize once at load, not on every question: cosine similarity is
    # then a single matrix-vector product.
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    return chunks, embeddings, BM25([c["text"] for c in chunks])


def _ranks(scores: np.ndarray) -> np.ndarray:
    ranks = np.empty(len(scores))
    ranks[np.argsort(-scores)] = np.arange(1, len(scores) + 1)
    return ranks


def load_embedder(name: str = EMBEDDING_MODEL):
    model = SentenceTransformer(name)
    model.query_prefix = QUERY_PREFIXES.get(name, "")
    return model


def top_k_chunks(question: str, model, chunks, embeddings, bm25, k=CANDIDATE_POOL):
    """Hybrid retrieval: cosine similarity and BM25 each rank every chunk,
    merged by reciprocal rank fusion. Measured on eval/: dense alone put the
    evidence in the top 15 for 14/18 questions, BM25 alone 17/18 but only
    3/8 on reworded questions; the fusion got 17/18 and 5/8 (best of both).
    Dense covers paraphrase; BM25 covers exact names, terms, and numbers —
    and MiniLM only reads a chunk's first 256 tokens, BM25 reads all of it."""
    query = getattr(model, "query_prefix", "") + question
    query_vec = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]
    # cosine similarity = dot product of normalized vectors
    fused = 1 / (60 + _ranks(embeddings @ query_vec)) + 1 / (60 + _ranks(bm25.scores(question)))
    top_indices = np.argsort(-fused)[:k]
    return [(chunks[i], fused[i]) for i in top_indices]


# A recency word plus one of the indicators fred.py actually serves. Laya
# underrates the live half of compound questions ("how does today's
# unemployment rate relate to the research on..." scored live=0.20).
ASKS_CURRENT_INDICATOR = re.compile(
    r"\b(current|currently|today'?s?|right now|latest|now)\b.*\b(unemployment|inflation|cpi|gdp|"
    r"interest rates?|fed(eral)? funds)\b|\b(unemployment|inflation|cpi|gdp|interest rates?|"
    r"fed(eral)? funds)\b.*\b(current|currently|today|right now|latest|now)\b",
    re.I,
)


def route_query(question: str, agent) -> str:
    """Laya's needs_live_data score separates cleanly (research questions
    <=0.20, live lookups >=0.60 on the eval set) but needs_documents is weak
    — usually <0.5 even for pure research questions. Requiring both to clear
    0.5 sent ~85% of research questions down the `both` route, spending a
    whole extra generation on an irrelevant live-data answer. So: route on the
    live score, search documents unless it's a confident pure lookup, and
    keep over-fetching (`both`) in the ambiguous middle."""
    result = agent.predict(question, ROUTE_QUESTIONS)
    live = result["answers"]["needs_live_data"]["noul"]
    docs = result["answers"]["needs_documents"]["noul"]
    needs_live = live >= 0.25 or bool(ASKS_CURRENT_INDICATOR.search(question))
    needs_docs = not (live >= 0.5 and docs < 0.25)
    if needs_live and needs_docs:
        return "both"
    return "live_data" if needs_live else "documents"


def rerank_with_laya(question: str, candidates: list, agent, k=TOP_K):
    states = [f"Query: {question}\n\nPassage: {chunk['text']}" for chunk, _ in candidates]
    results = agent.predict_batch(states, RELEVANCE_QUESTION)
    scored = [
        (chunk, result["answers"]["relevance"]["score"])
        for (chunk, _cos_score), result in zip(candidates, results)
    ]
    if any(np.isnan(score) for _, score in scored):
        print("  WARNING: Laya returned NaN scores (likely a GPU/memory error) — "
              "falling back to retrieval order instead of reranking.")
        return candidates[:k]
    # Fuse Laya's ranking with the retrieval ranking (candidates arrive in
    # retrieval order) rather than letting Laya overrule it: Laya only reads
    # a chunk's first ~470 of its tokens (~44%), so it dropped chunks that
    # BM25 — which reads the whole chunk — had ranked 3rd. On eval/, the fact
    # reached the prompt for 18/26 questions fused vs 16/26 with Laya alone.
    laya_ranks = _ranks(np.array([score for _, score in scored]))
    fused = [1 / (RERANK_RRF_C + i + 1) + 1 / (RERANK_RRF_C + r) for i, r in enumerate(laya_ranks)]
    order = np.argsort(fused)[::-1][:k]
    return [scored[i] for i in order]


def count_tokens(tokenizer, text: str) -> int:
    return len(tokenizer(text, add_special_tokens=False)["input_ids"])


def build_doc_context(question: str, results: list, tokenizer, num_ctx: int | None = None) -> str:
    """Packs reranked chunks, best first, into what's left of num_ctx
    (default NUM_CTX) after the instructions, question, and ANSWER_RESERVE;
    the last chunk that doesn't fully fit is cut at a token boundary rather
    than dropped."""
    budget = ((num_ctx or NUM_CTX) - ANSWER_RESERVE - CHAT_TEMPLATE_TOKENS
              - count_tokens(tokenizer, PROMPT_TEMPLATE.format(context="", question=question)))
    parts = []
    for chunk, _score in results:
        piece = f"(from {paper_label(chunk['source'])}) {chunk['text']}"
        enc = tokenizer(piece, add_special_tokens=False, return_offsets_mapping=True)
        cost = len(enc["input_ids"]) + 2  # + the "\n\n" separator
        if cost <= budget:
            parts.append(piece)
            budget -= cost
            continue
        if budget > 64:  # a meaningful fragment still fits
            cut = enc["offset_mapping"][budget - 3][1]
            parts.append(piece[:cut] + " ...")
        break
    return "\n\n".join(parts)


def ask_llm(question: str, context: str, tokenizer) -> str:
    """Streams the answer to the terminal as it's generated and returns it."""
    prompt = PROMPT_TEMPLATE.format(context=context, question=question)
    prompt_tokens = count_tokens(tokenizer, prompt) + CHAT_TEMPLATE_TOKENS
    response = _ollama.post(OLLAMA_URL, stream=True, json={
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        "keep_alive": KEEP_ALIVE,
        # low temperature: stick to what's in the context instead of
        # drifting into vague hedges or confidently inventing details.
        # num_predict caps the answer at the room actually left in num_ctx,
        # so it ends cleanly instead of triggering a context shift.
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2,
                    "num_predict": max(ANSWER_RESERVE, NUM_CTX - prompt_tokens)},
    })
    response.raise_for_status()
    pieces = []
    for line in response.iter_lines():
        if not line:
            continue
        msg = json.loads(line)
        if "error" in msg:
            raise RuntimeError(f"Ollama error: {msg['error']}")
        pieces.append(msg.get("response", ""))
        print(msg.get("response", ""), end="", flush=True)
        if msg.get("done") and msg.get("prompt_eval_count", 0) >= NUM_CTX - 1:
            print("\n  WARNING: prompt filled num_ctx — Ollama may have truncated it.")
    print()
    return "".join(pieces)


def ask_groq(question: str, context: str) -> str:
    """Streams Groq's answer, from a fixed context and no tools, to the
    terminal and returns it. Used by eval/run_eval.py to compare generators
    on identical context; ask.py itself uses analyst.py. Raises
    GroqUnavailable on any network or API failure."""
    prompt = PROMPT_TEMPLATE.format(context=context, question=question)
    response = groq_post({"temperature": 0.2, "max_tokens": GROQ_MAX_TOKENS, "reasoning_effort": "low",
                          "messages": [{"role": "user", "content": prompt}]}, stream=True)
    pieces = []
    try:
        # OpenAI-style server-sent events: "data: {json}" lines, then "data: [DONE]"
        for line in response.iter_lines():
            if not line.startswith(b"data: "):
                continue
            if line == b"data: [DONE]":
                break
            msg = json.loads(line[len(b"data: "):])
            if "error" in msg:
                raise GroqUnavailable(f"Groq error: {msg['error']}")
            piece = msg["choices"][0]["delta"].get("content") or ""
            pieces.append(piece)
            print(piece, end="", flush=True)
    except requests.RequestException as e:
        print()
        raise GroqUnavailable("connection dropped mid-answer") from e
    print()
    return "".join(pieces)


def unload_llm():
    try:
        _ollama.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass


def warm_up(agent, model):
    """phi3.5 holds ~3.6GB of unswappable GPU memory while it generates, so
    on 8GB the OS pages Laya's weights out to swap. Touching them right after
    each answer, while the user types, pages them back in — otherwise the next
    question's routing and reranking pay for it (measured: routing 7-12s vs
    0.5s, reranking 26-33s vs 8-14s)."""
    agent.predict("warm up", ROUTE_QUESTIONS)
    model.encode(["warm up"])


def paper_searcher(model, chunks, embeddings, bm25, agent, tokenizer):
    """analyst.py's paper-search tool: the same retrieval + rerank as the local
    path, skipping chunks already shown this question. Shared with
    eval/benchmark.py so the benchmark tests exactly what ask.py runs."""
    for i, chunk in enumerate(chunks):
        chunk["index"] = i  # stable id for the analyst's "already shown" set

    def search_papers(query: str, exclude: set[int]):
        candidates = [(c, s) for c, s in top_k_chunks(query, model, chunks, embeddings, bm25)
                      if c["index"] not in exclude]
        results = rerank_with_laya(query, candidates, agent) if candidates else []
        context = build_doc_context(query, results, tokenizer, AGENT_PAPERS_CTX)
        included = [c for c, _ in results if c["text"][:60] in context]  # packing may drop the tail
        return (context, list(dict.fromkeys(c["source"] for c in included)),
                [c["index"] for c in included])
    return search_papers


def load_models(pool: ThreadPoolExecutor) -> SimpleNamespace:
    """Loads the index, embedder, Laya and tokenizer (plus Atlas and World Bank
    with a Groq key). Shared with web.py: run one or the other, never both —
    two copies of the models don't fit in 8GB."""
    # Load everything concurrently — mostly disk I/O and Python import work.
    # Same set of models resident as before; they're just loaded in parallel.
    # Laya is forced to CPU: Ollama's phi3.5 uses the GPU (Metal) for
    # generation, and on Apple Silicon's unified memory, two processes
    # competing for the same Metal command queue on an 8GB machine can exhaust
    # GPU memory (as happened in testing). Laya is small (421M params) — running
    # it on CPU costs a little latency but avoids that contention entirely.
    print("Loading models...")
    index_f = pool.submit(load_index)
    model_f = pool.submit(load_embedder)
    agent_f = pool.submit(laya.load, LAYA_MODEL, device="cpu")
    tok_f = pool.submit(AutoTokenizer.from_pretrained, OLLAMA_TOKENIZER)
    atlas_f = pool.submit(_optional, Atlas) if USE_GROQ else None
    wb_f = pool.submit(WorldBank) if USE_GROQ else None
    chunks, embeddings, bm25 = index_f.result()
    print(f"Loaded {len(chunks)} chunks.")
    return SimpleNamespace(chunks=chunks, embeddings=embeddings, bm25=bm25, model=model_f.result(),
                           agent=agent_f.result(), tokenizer=tok_f.result(),
                           atlas=atlas_f.result() if atlas_f else None, wb=wb_f.result() if wb_f else None)


def _optional(load):
    """Data that may not be downloaded yet: None instead of a crash (the tools say it's missing)."""
    try:
        return load()
    except FileNotFoundError as e:
        print(f"  {e} — that part is off.")
        return None


def make_analyst(m: SimpleNamespace) -> Analyst | None:
    if not USE_GROQ:
        return None
    searcher = paper_searcher(m.model, m.chunks, m.embeddings, m.bm25, m.agent, m.tokenizer) if m.chunks else None
    return Analyst(searcher, m.atlas, m.wb)


def answer_locally(question: str, m: SimpleNamespace, pool: ThreadPoolExecutor) -> str:
    """The offline pipeline: Laya routes, then FRED and/or documents -> phi3.5."""
    route = route_query(question, m.agent)
    print(f"\n  Router decision: {route}")

    # FRED is network-bound: start it now so it overlaps retrieval.
    fred_f = pool.submit(fred.get_indicator_summary) if route in ("live_data", "both") else None

    doc_context = None
    if route in ("documents", "both") and not m.chunks:
        print("\n  (No paper library on this computer: answering from live data only.)")
        fred_f = fred_f or pool.submit(fred.get_indicator_summary)
    elif route in ("documents", "both"):
        candidates = top_k_chunks(question, m.model, m.chunks, m.embeddings, m.bm25)
        print(f"\n  Hybrid retrieval (cosine + BM25) top {len(candidates)}:")
        for chunk, score in candidates:
            print(f"    [{score:.3f}] {chunk['source']}: {' '.join(chunk['text'][:90].split())[:70]}...")

        results = rerank_with_laya(question, candidates, m.agent)
        print(f"\n  Laya reranked, keeping top {len(results)}:")
        for chunk, score in results:
            print(f"    [{score:.3f}] {chunk['source']}: {' '.join(chunk['text'][:90].split())[:70]}...")

        doc_context = build_doc_context(question, results, m.tokenizer)

    print(f"\n  Asking {OLLAMA_MODEL}...")
    answers = []
    if fred_f:
        try:
            fred_context = f"Live economic indicators (from FRED):\n{fred_f.result()}"
        except Exception as e:  # network/API hiccup: don't kill the session
            print(f"\n  WARNING: couldn't fetch FRED data ({e}); skipping the live-data answer.")
            fred_f = None
    if fred_f:
        # `both` questions get two separate, single-purpose generations:
        # one asked to ground itself in live numbers AND document
        # synthesis at once proved unreliable in testing (it would drop
        # the FRED figure, or fabricate citations/examples to compensate).
        print(f"\nAnswer{' (from live data)' if doc_context is not None else ''}:")
        answers.append(ask_llm(question, fred_context, m.tokenizer))
    if doc_context is not None:
        print(f"\nAnswer{' (from research documents)' if fred_f else ''}:")
        answers.append(ask_llm(question, doc_context, m.tokenizer))
    return "\n\n".join(answers)


def main():
    pool = ThreadPoolExecutor(max_workers=4)
    m = load_models(pool)
    agent, model = m.agent, m.model
    warm_f = pool.submit(warm_up, agent, model)  # first calls pay one-time setup

    analyst = make_analyst(m)
    print(f"Answering with {GROQ_MODEL} via Groq (tools: papers, World Bank, FRED, Opportunity Atlas); "
          f"local {OLLAMA_MODEL} if Groq is unreachable." if analyst else
          f"Answering locally with {OLLAMA_MODEL} (set GROQ_API_KEY in .env for the online analyst).")

    print("Ready. Type a question (or 'quit' to exit).\n")
    try:
        while True:
            try:
                question = input("Question: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() in ("quit", "exit"):
                break
            if not question:
                continue
            warm_f.result()  # normally finished long before the user hits Enter

            if analyst:
                try:
                    print()
                    analyst.run(question)
                except GroqUnavailable as e:
                    print(f"\n  (Groq unavailable — {e}. Answering locally with {OLLAMA_MODEL}.)")
                    analyst.remember(question, answer_locally(question, m, pool))
            else:
                answer_locally(question, m, pool)
            print("-" * 60)
            warm_f = pool.submit(warm_up, agent, model)
    finally:
        unload_llm()  # in case of Ctrl-C mid-answer
        pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
