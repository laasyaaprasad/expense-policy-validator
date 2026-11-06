"""Run experiment 4: `uv run python -m experiments.graph_retrieval` (or `make graph-retrieval`).

Builds the policy graph three ways, runs every retriever in PLAN.md in oracle mode, system mode and
on CORD, and scores recall, size, decisions and edge quality. The engine and rules.yaml are the
submitted ones; only the rules chosen differ. Offline once cache/ holds every reply and vector; a miss
needs OPENROUTER_API_KEY. Writes results/; never touches validator/, rules/ or outputs/.
"""

import json
import re
import sys
from pathlib import Path

from evaluation import cord
from evaluation.blame import load_classified
from evaluation.evaluate import decision_records, receipt_matches
from evaluation.gold import in_scope, load_gold
from evaluation.oracle import load_attributes, oracle_receipts
from experiments.cascade.embed import Embedder
from experiments.graph_retrieval.extract import extract
from experiments.graph_retrieval.graph import PREDICATES, from_config, from_extraction
from experiments.graph_retrieval.retrieve import K, BM25, embedding_ranked, graph_retrieve, query_text
from validator.data import ROOT, load_receipts, read_jsonl
from validator.engine import evaluate
from validator.llm import LLM, LLMError, ledger_total
from validator.retriever import retrieve
from validator.rules_config import RulesConfig, load_rules
from validator.policies import load_policies

import yaml

HERE = Path(__file__).resolve().parent
RESULTS, CACHE_DIR, LEDGER_PATH = HERE / "results", HERE / "cache", HERE / "spend.jsonl"
EXPERIMENT_3_CONFIG = ROOT / "experiments" / "policy_config" / "results" / "rules_retrieval.yaml"
FIRED = ("raised", "signal", "waived")


def _llm() -> LLM:
    try:
        return LLM(cache_dir=CACHE_DIR, ledger_path=LEDGER_PATH)
    except LLMError as error:
        print(f"{error}. Replaying the cache instead.")
        return LLM.offline(cache_dir=CACHE_DIR, ledger_path=LEDGER_PATH)


def needed(classified, config, checkable) -> dict[str, set[str]]:
    """PLAN.md: the rules whose check finds something when the engine runs every rule."""
    trace: dict = {}
    evaluate(classified, config, None, trace=trace)
    return {rid: {rule for rule, step in t["rules"].items() if step["result"] in FIRED and rule in checkable}
            for rid, t in trace.items()}


def _decision(record: dict) -> tuple:
    return (record["disposition"], tuple(sorted((e["rule_id"], tuple(sorted(e["line_ids"]))) for e in record["exceptions"])),
            tuple(sorted(s["rule_id"] for s in record["review_signals"])))


def score(retrieved: dict[str, list[str]], need: dict[str, set[str]]) -> dict:
    total = sum(len(n) for n in need.values())
    hit = sum(len(n & set(retrieved[rid])) for rid, n in need.items())
    missed = {rid: sorted(n - set(retrieved[rid])) for rid, n in need.items() if n - set(retrieved[rid])}
    sizes = [len(retrieved[rid]) for rid in need]
    return {"recall": {"k": hit, "n": total}, "receipts_full_recall": {"k": len(need) - len(missed), "n": len(need)},
            "missed": missed, "rules_per_receipt": {"mean": round(sum(sizes) / len(sizes), 2), "min": min(sizes), "max": max(sizes)}}


def edge_quality(store, reference) -> dict:
    out = {}
    for predicate in PREDICATES:
        if predicate == "cites":
            continue  # the parser's, identical in every graph
        got, want = store.of(predicate), reference.of(predicate)
        out[predicate] = {"right": len(got & want), "extra": len(got - want), "missing": len(want - got),
                          "extra_edges": sorted(f"{s} -> {o}" for s, _, o in got - want)[:15],
                          "missing_edges": sorted(f"{s} -> {o}" for s, _, o in want - got)[:15]}
    return out


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    policies, config, gold = load_policies(), load_rules(), load_gold()
    checkable = {r.id for r in config.rules if r.kind != "mechanic"}

    # --- the three settings ----------------------------------------------------------------------
    receipts = load_receipts()
    glosses = {row["line_id"]: row.get("english") for row in read_jsonl(ROOT / "outputs" / "classifications.jsonl")}
    cord_receipts = load_receipts(cord.RECEIPTS_PATH)
    cord_classified, cord_rows = cord.load_system(cord_receipts)
    glosses |= {i: r.get("english") for i, r in cord_rows.items()}
    settings = {"oracle": oracle_receipts(receipts, gold, load_attributes()), "system": load_classified(), "cord": cord_classified}
    need = {name: needed(classified, config, checkable) for name, classified in settings.items()}

    # --- the graphs ------------------------------------------------------------------------------
    llm = _llm()
    extracted, failures = {}, {}
    for rule_id in sorted(checkable):
        try:
            extracted[rule_id] = extract(llm, policies[rule_id], config, set(policies))
        except LLMError as error:
            failures[rule_id] = str(error)[:300]
    graphs = {
        "graph_rules_yaml": from_config(config, policies),
        "graph_extracted": from_extraction(extracted, policies),
        "graph_experiment3": from_config(RulesConfig.model_validate(yaml.safe_load(EXPERIMENT_3_CONFIG.read_text())), policies),
    }
    for name, store in graphs.items():
        store.save(RESULTS / f"{name}.tsv")

    # --- the text retrievers ---------------------------------------------------------------------
    rule_list = [policies[i] for i in sorted(checkable)]
    bm25 = BM25(rule_list)
    embedder = Embedder(cache_path=CACHE_DIR / "embeddings.npz", ledger_path=LEDGER_PATH)
    rule_vectors = embedder.embed([f"{r.title}. {' '.join(re.sub(r'[*`]', '', r.text).split())}" for r in rule_list])
    rule_ids = [r.id for r in rule_list]

    ranked: dict[str, dict[str, dict[str, list[str]]]] = {"bm25": {}, "embeddings": {}}
    for name, classified in settings.items():
        queries = {c.receipt.receipt_id: query_text(c, config, glosses) for c in classified}
        ranked["bm25"][name] = {rid: bm25.ranked(q) for rid, q in queries.items()}
        vectors = embedder.embed(list(queries.values()))
        ranked["embeddings"][name] = {rid: embedding_ranked(v, rule_vectors, rule_ids) for rid, v in zip(queries, vectors)}

    # --- every arm in every setting --------------------------------------------------------------
    def arms_for(name: str, classified) -> dict[str, dict[str, list[str]]]:
        by_id = {c.receipt.receipt_id: c for c in classified}
        arms = {"structured_index": {rid: sorted(set(retrieve(c, config, policies).rule_ids) & checkable) for rid, c in by_id.items()}}
        arms |= {g: {rid: graph_retrieve(c, store, config, checkable) for rid, c in by_id.items()} for g, store in graphs.items()}
        arms["bm25_top10"] = {rid: sorted(r[:K]) for rid, r in ranked["bm25"][name].items()}
        arms["embeddings_top10"] = {rid: sorted(r[:K]) for rid, r in ranked["embeddings"][name].items()}
        arms["every_rule"] = {rid: sorted(checkable) for rid in by_id}
        return arms

    results: dict = {"settings": {}}
    for name, classified in settings.items():
        arms = arms_for(name, classified)
        baseline = decision_records(evaluate(classified, config, arms["structured_index"]))
        block = {}
        for arm, retrieved in arms.items():
            entry = score(retrieved, need[name])
            records = decision_records(evaluate(classified, config, retrieved))
            if name == "cord":
                same = sum(_decision(records[rid]) == _decision(baseline[rid]) for rid in records)
                entry["decisions_same_as_submitted"] = {"k": same, "n": len(records)}
            else:
                scored = [rid for rid in records if in_scope(rid)]
                right = [rid for rid in scored if receipt_matches(records[rid], gold[rid])]
                entry["exactly_right"] = {"k": len(right), "n": len(scored)}
                entry["wrong"] = sorted(set(scored) - set(right))
            block[arm] = entry
        # Side analyses, after the fact: two hops for the extracted graph, and recall at every k.
        two_hop = {c.receipt.receipt_id: graph_retrieve(c, graphs["graph_extracted"], config, checkable, hops=2) for c in classified}
        block["graph_extracted_two_hops"] = score(two_hop, need[name])
        for text in ("bm25", "embeddings"):
            curve = {}
            for k in (5, 10, 15, 20, 25, 30, len(checkable)):
                curve[k] = score({rid: r[:k] for rid, r in ranked[text][name].items()}, need[name])["receipts_full_recall"]
            worst = max((max((ranked[text][name][rid].index(rule) for rule in n), default=-1) + 1) for rid, n in need[name].items())
            block[f"{text}_recall_by_k"] = {"receipts_full_recall": curve, "k_for_full_recall_on_every_receipt": worst}
        results["settings"][name] = block

    results["graph_quality"] = {g: edge_quality(graphs[g], graphs["graph_rules_yaml"]) for g in ("graph_extracted", "graph_experiment3")}
    results["extraction_failures"] = failures
    results["graph_size"] = {g: {p: len(s.of(p)) for p in PREDICATES} for g, s in graphs.items()}
    results["spend"] = {"usd": ledger_total(LEDGER_PATH),
                        "rows": sum(1 for r in LEDGER_PATH.read_text().splitlines() if r.strip()) if LEDGER_PATH.exists() else 0}
    results["lines_of_code"] = {"submitted structured index (validator/retriever.py)": _lines(ROOT / "validator" / "retriever.py"),
                                "graph store, extraction and traversal": sum(_lines(HERE / f) for f in ("graph.py", "extract.py"))
                                + _lines(HERE / "retrieve.py")}
    (RESULTS / "metrics.json").write_text(json.dumps(results, indent=1, default=str) + "\n", encoding="utf-8")
    (RESULTS / "summary.md").write_text(summary(results), encoding="utf-8")
    print(summary(results))
    return 0


def _lines(path: Path) -> int:
    return sum(1 for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#"))


def _k(x: dict) -> str:
    return f"{x['k']} / {x['n']}"


ARM_ORDER = ("structured_index", "graph_rules_yaml", "graph_extracted", "graph_experiment3", "bm25_top10", "embeddings_top10", "every_rule")


def summary(r: dict) -> str:
    s = r["settings"]
    out = ["# Experiment 4: results", "", "Generated by `make graph-retrieval`. Read with PLAN.md.", "",
           "| Retriever | Recall (oracle) | Receipts fully covered (oracle / system / CORD) | Rules per receipt | 28 right (oracle / system) | CORD same as submitted |",
           "|---|---|---|---|---|---|"]
    for arm in ARM_ORDER:
        o, y, c = s["oracle"][arm], s["system"][arm], s["cord"][arm]
        out.append(f"| {arm} | {_k(o['recall'])} | {_k(o['receipts_full_recall'])} · {_k(y['receipts_full_recall'])} · "
                   f"{_k(c['receipts_full_recall'])} | {y['rules_per_receipt']['mean']} ({y['rules_per_receipt']['min']}–"
                   f"{y['rules_per_receipt']['max']}) | {_k(o['exactly_right'])} · {_k(y['exactly_right'])} | "
                   f"{_k(c['decisions_same_as_submitted'])} |")
    out += ["", "**k needed for every receipt to be fully covered:** " + "; ".join(
        f"{t} {s[n][t + '_recall_by_k']['k_for_full_recall_on_every_receipt']} ({n})" for t in ("bm25", "embeddings") for n in s), "",
        "## Edge quality against rules.yaml (right · extra · missing)", "", "| Edge | extracted | experiment 3 |", "|---|---|---|"]
    q = r["graph_quality"]
    for predicate in q["graph_extracted"]:
        a, b = q["graph_extracted"][predicate], q["graph_experiment3"][predicate]
        out.append(f"| {predicate} | {a['right']} · {a['extra']} · {a['missing']} | {b['right']} · {b['extra']} · {b['missing']} |")
    out += ["", f"**Spend:** ${r['spend']['usd']:.4f}; extraction failures: {len(r['extraction_failures'])}."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
