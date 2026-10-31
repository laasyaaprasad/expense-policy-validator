.PHONY: test oracle dev items smoke run eval baseline app

# Offline tests: no API key needed, no model calls.
test:
	uv run pytest -m "not llm"

# Engine on the gold categories (M4).
oracle:
	uv run python -m evaluation.oracle

# Classify the hand-written dev set and apply the threshold rule (M9). Needs a key on a cache miss.
dev:
	uv run python -m evaluation.dev

# v1 classifications against the answer key, item by item (M9). Offline.
items:
	uv run python -m evaluation.items

# Live model capability checks (M7). Needs a key in .env.
smoke:
	uv run pytest -m llm -s -q tests/test_llm_smoke.py

# Full pipeline on all 32 receipts (M10). Replays the committed cache; with no key in .env it runs offline.
run:
	uv run python -m validator.pipeline

# Part 3 evaluation from committed outputs, offline (M11): items.json, blame.json, metrics.json.
eval:
	uv run python -m evaluation.items > /dev/null
	uv run python -m evaluation.blame > /dev/null
	uv run python -m evaluation.evaluate

# Keyword baseline through the same engine (M12).
baseline:
	@echo "Not built yet (M12)"; exit 1

# Results viewer (M13).
app:
	@echo "Not built yet (M13)"; exit 1
