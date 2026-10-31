"""The organisers' data is not in the repo. Stop with one clear message, not a wall of import errors."""

import pytest

from validator.data import DATA_DIR, POLICY_DIR, RECEIPTS_PATH, TAXONOMY_PATH


def pytest_configure(config):
    missing = [p for p in (RECEIPTS_PATH, TAXONOMY_PATH, POLICY_DIR) if not p.exists()]
    if missing:
        raise pytest.UsageError(
            f"The organisers' data is missing ({', '.join(str(p.relative_to(DATA_DIR.parent)) for p in missing)}). "
            "It is not committed: copy their data/ folder to the repo root, then rerun.")
