.PHONY: test oracle smoke run eval baseline app

# Offline tests: no API key needed, no model calls.
test:
	uv run pytest -m "not llm"

# Engine on the gold categories (M4).
oracle:
	@echo "Not built yet (M4)"; exit 1

# Live model capability checks (M7). Needs a key in .env.
smoke:
	@echo "Not built yet (M7)"; exit 1

# Full pipeline; reuses the cache (M10).
run:
	@echo "Not built yet (M10)"; exit 1

# Metrics and blame report (M11).
eval:
	@echo "Not built yet (M11)"; exit 1

# Keyword baseline through the same engine (M12).
baseline:
	@echo "Not built yet (M12)"; exit 1

# Results viewer (M13).
app:
	@echo "Not built yet (M13)"; exit 1
