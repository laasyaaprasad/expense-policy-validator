.PHONY: test oracle dev items smoke run eval baseline cord cascade verifier policy-config graph-retrieval paraphrase semantic-first semantic-v2 semantic-v3 semantic-v31 app

# Offline tests: no API key needed, no model calls.
test:
	uv run pytest -m "not llm"

# Engine on the gold categories (oracle mode).
oracle:
	uv run python -m evaluation.oracle

# Classify the hand-written dev set and apply the threshold rule. Replays the cache; a key is needed only on a miss.
dev:
	uv run python -m evaluation.dev

# The committed classifications against the answer key, item by item. Offline.
items:
	uv run python -m evaluation.items

# Live model capability checks. Needs a key in .env.
smoke:
	uv run pytest -m llm -s -q tests/test_llm_smoke.py

# Full pipeline on all 32 receipts. Replays the committed cache; with no key in .env it runs offline.
run:
	uv run python -m validator.pipeline

# Part 3 evaluation from committed outputs, offline: items.json, blame.json, metrics.json.
eval:
	uv run python -m evaluation.items > /dev/null
	uv run python -m evaluation.blame > /dev/null
	uv run python -m evaluation.evaluate

# Keyword baseline through the same engine: outputs/baseline.jsonl. No model calls.
baseline:
	uv run python -m validator.baseline

# CORD pilot: the system on 100 real receipts, scored against the assistant's labels, not yet checked by hand (cord/README.md). Offline;
# `uv run python -m evaluation.cord --run` reruns the pipeline first.
cord:
	uv run python -m evaluation.cord

# Experiment 1, after the freeze: a cheap embedding stage that escalates uncertain lines to the LLM
# (experiments/cascade/PLAN.md). Offline once its embeddings are cached; changes nothing submitted.
cascade:
	uv run python -m experiments.cascade

# Experiment 2, after the freeze: a decision model checks each restricted line with one yes/no question
# (experiments/verifier/PLAN.md). Offline once its answers are cached; changes nothing submitted.
verifier:
	uv run python -m experiments.verifier

# Experiment 3, after the freeze: an LLM pipeline from the policy text to the standardized config, scored
# against rules.yaml and end to end (experiments/policy_config/PLAN.md). Offline once cached; changes nothing submitted.
policy-config:
	uv run python -m experiments.policy_config

# Experiment 4, after the freeze: a policy knowledge graph for retrieval, against the structured index, BM25 and
# embeddings (experiments/graph_retrieval/PLAN.md). Offline once cached; changes nothing submitted.
graph-retrieval:
	uv run python -m experiments.graph_retrieval

# Experiment 5, after the freeze: the 28 scored receipts with every itemized line reworded, to test whether 65 / 65
# depended on the exact wording (experiments/paraphrase/PLAN.md). Needs every row of review.csv checked by hand first;
# offline once cached. Changes nothing submitted.
paraphrase:
	uv run python -m experiments.paraphrase

# Experiment 6, after the freeze: a semantic first stage that decides clear, safe lines from labelled anchors and
# sends the rest to the model at temperature 0 (experiments/semantic_first/PLAN.md). Offline once cached; changes
# nothing submitted.
semantic-first:
	uv run python -m experiments.semantic_first

# Experiment 7: a local semantic lookup decides the clear, safe lines and the model the rest, against main's
# classifier, on every test set including CORD's held-out test split (experiments/semantic_v2/PLAN.md).
# Needs the semantic dependency group (PyTorch, sentence-transformers) and the local model.
semantic-v2:
	uv run --group semantic python -m experiments.semantic_v2

# Experiment 8: version 3 (memory of reviewed lines, careful generalisation, the model as fallback), streamed over 400
# fresh CORD training receipts with learning after each batch, against main (experiments/semantic_v3/PLAN.md).
semantic-v3:
	uv run --group semantic python -m experiments.semantic_v3

# Experiment 9: a stricter same-item rule for the lookup's memory, streamed over 200 fresh CORD receipts against the
# lookup as it is and the model on every line (experiments/semantic_v31/PLAN.md).
semantic-v31:
	uv run --group semantic python -m experiments.semantic_v31

# Results viewer: receipt list, receipt detail, metrics. Reads committed outputs; no model calls.
app:
	uv run streamlit run app.py
