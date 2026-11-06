.PHONY: test oracle dev items smoke run eval baseline cord cascade verifier policy-config app

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

# Results viewer: receipt list, receipt detail, metrics. Reads committed outputs; no model calls.
app:
	uv run streamlit run app.py
