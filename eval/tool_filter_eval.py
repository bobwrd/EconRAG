"""Could Laya pick which tools to send to Groq? Zero Groq tokens: Laya only.

Every Groq request re-sends all tool schemas (~2K tokens). The idea: Laya asks one
yes/no question per tool group before the analyst starts, and only the groups it says
yes to are sent. That saves tokens only if Laya almost never drops a tool the question
needs, so this measures, per group, how Laya's score separates questions that need it
from questions that don't.

Questions: the benchmark (45; tool needs labeled by hand below, checked against the
tools the analyst used in the oct7 run), plus eval/questions.json (23) and
eval/dev_questions.json (11) as a second set nobody tuned on.

    .venv/bin/python eval/tool_filter_eval.py           # score (Laya on CPU, ~1-2 min), then report
    .venv/bin/python eval/tool_filter_eval.py --report  # report from the saved scores
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCORES = ROOT / "eval" / "results" / "tool_filter_scores.json"

GROUPS = {
    "data": ["search_data", "get_data"],
    "counties": ["county_profile", "rank_counties", "correlate_counties"],
    "papers": ["search_papers"],
    "literature": ["search_literature"],
    "evaluations": ["search_evaluations"],
    "python": ["run_python"],
}

GROUP_QUESTIONS = {
    "data": "Does answering this question need statistics for a country, region or the US "
            "economy, such as GDP, poverty, inflation, unemployment, population, health or "
            "education figures, or their history?",
    "counties": "Is this question about specific US counties or comparing US counties, such as "
                "upward mobility of children in a county?",
    "papers": "Does answering this question need findings from economics research papers or "
              "studies?",
    "literature": "Does this question name a specific research paper or authors, or ask what "
                  "published research finds?",
    "evaluations": "Does this question ask whether a program or policy for poor people works, "
                   "such as cash transfers, microcredit, deworming or schooling programs?",
    "python": "Does answering this question need a calculation, such as an average growth rate, "
              "doubling time, ratio or weighted average?",
}

# Each requirement is a set of groups, at least one of which must be kept.
D, C, P, L, E, PY = ({g} for g in GROUPS)
LABELS = {
    **{q: [D] for q in [
        "wb_ken_gdppc", "wb_ind_gdppc_ppp", "wb_nga_lifeexp", "wb_bgd_u5mr", "wb_bra_gini",
        "wb_ssa_elec", "wb_pak_primary", "wb_npl_remit", "wb_arg_infl", "wb_vnm_growth",
        "wb_uga_agri_emp", "wb_ner_fertility", "wb_eth_poverty", "wb_nga_pop", "fred_unrate_covid",
        "fred_emratio", "trap_us_employment", "trap_us_inflation", "trap_pp", "trap_nga_poverty_today",
        "trap_ind_ppp_vs_market", "comp_bgd_ind_ratio", "peer_ken_u5mr", "lr_kor_gha", "lr_chn_1950",
        "lr_gbr_1820", "lr_chn_ind_cross"]},
    **{q: [C] for q in ["atlas_fulton", "atlas_utah_top", "atlas_math_corr", "trap_cohort"]},
    "trap_fake_paper": [P | L],
    "comp_chn_cagr": [D, PY], "comp_eth_double": [D, PY], "comp_weighted_lifeexp": [D, PY],
    "lit_microcredit": [P | E], "lit_deworming": [P | L], "lit_cash_inflation": [P | E],
    "lit_graduation": [P | E], "lit_poverty_line": [P | L],
}


def questions():
    """(set name, id, question, requirements)."""
    out = []
    evalq = {q["id"]: q for q in json.loads((ROOT / "eval/questions.json").read_text())}
    for q in json.loads((ROOT / "eval/benchmark_questions.json").read_text()):
        qid = q.get("id") or q["from_eval"]
        text = q.get("question") or evalq[qid]["question"]
        out.append(("benchmark", qid, text, LABELS.get(qid, [P])))   # from_eval ones: library papers
    route_needs = {"documents": [P], "live_data": [D], "both": [D, P]}
    for q in evalq.values():
        out.append(("held_out", q["id"], q["question"], route_needs[q["route"]]))
    for q in json.loads((ROOT / "eval/dev_questions.json").read_text()):
        out.append(("held_out", q["id"], q["question"], [P | E | L]))
    return out


def group_tokens():
    """Each group's share of the ~2K schema tokens (by JSON length)."""
    from analyst import TOOLS
    size = {t["function"]["name"]: len(json.dumps(t)) for t in TOOLS}
    total = sum(size.values())
    return {g: round(1994 * sum(size[n] for n in names) / total) for g, names in GROUPS.items()}


def score():
    import laya
    from ask import LAYA_MODEL
    agent = laya.load(LAYA_MODEL, device="cpu")
    qs = {g: {"type": "noul", "instructions": text} for g, text in GROUP_QUESTIONS.items()}
    rows = []
    for setname, qid, text, _ in questions():
        answers = agent.predict(text, qs)["answers"]
        rows.append({"set": setname, "id": qid, **{g: round(answers[g]["noul"], 3) for g in GROUPS}})
        print(qid, {g: rows[-1][g] for g in GROUPS}, flush=True)
    SCORES.parent.mkdir(exist_ok=True)
    SCORES.write_text(json.dumps({"questions": GROUP_QUESTIONS, "scores": rows}, indent=1))


def kept_ok(row, reqs, thresholds):
    kept = {g for g in GROUPS if row[g] >= thresholds[g]}
    return all(req & kept for req in reqs), kept


def report():
    saved = json.loads(SCORES.read_text())
    if saved["questions"] != GROUP_QUESTIONS:
        sys.exit("Group questions changed since scoring: run without --report.")
    rows = {(r["set"], r["id"]): r for r in saved["scores"]}
    qs = questions()
    tokens = group_tokens()
    print("Schema tokens per group:", tokens)

    print("\nSeparation (benchmark): lowest score among questions that need the group "
          "vs highest among those that don't")
    for g in GROUPS:
        need = [rows["benchmark", qid][g] for s, qid, _, reqs in qs if s == "benchmark" and {g} in reqs]
        rest = [rows["benchmark", qid][g] for s, qid, _, reqs in qs
                if s == "benchmark" and not any(g in req for req in reqs)]
        print(f"  {g:12} need: min {min(need) if need else '-':<6} (n={len(need)})   "
              f"don't: max {max(rest):.3f} median {sorted(rest)[len(rest) // 2]:.3f} (n={len(rest)})")

    for always_data in (False, True):
        print(f"\n{'Data tools always sent; ' if always_data else 'All groups filtered; '}"
              "same threshold for every group")
        for t in (0.05, 0.1, 0.15, 0.2, 0.3, 0.5):
            th = {g: (0 if always_data and g == "data" else t) for g in GROUPS}
            for setname in ("benchmark", "held_out"):
                sub = [(qid, reqs) for s, qid, _, reqs in qs if s == setname]
                ok, saved_tok, misses = 0, 0, []
                for qid, reqs in sub:
                    good, kept = kept_ok(rows[setname, qid], reqs, th)
                    ok += good
                    if not good:
                        misses.append(qid)
                    saved_tok += sum(tokens[g] for g in GROUPS if g not in kept)
                print(f"  t={t:<4} {setname:9} right tools kept {ok}/{len(sub)}, "
                      f"schema tokens saved per request {saved_tok / len(sub):.0f}"
                      + (f"  missed: {', '.join(misses[:8])}{' ...' if len(misses) > 8 else ''}"
                         if misses else ""))


if __name__ == "__main__":
    if "--report" not in sys.argv:
        score()
    report()
