.PHONY: test oracle dev items smoke run eval baseline cord app

# Offline tests: no API key needed, no model calls.
test:
	uv run pytest -m "not llm"

# Engine on the gold categories (oracle mode).
oracle:
	uv run python -m evaluation.oracle

# Classify the hand-written dev set and apply the threshold rule. Needs a key on a cache miss.
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

# CORD pilot: the system on 100 real receipts, scored against hand labels (cord/README.md). Offline;
# `uv run python -m evaluation.cord --run` reruns the pipeline first.
cord:
	uv run python -m evaluation.cord

# Results viewer: receipt list, receipt detail, metrics. Reads committed outputs; no model calls.
app:
	uv run streamlit run app.py
