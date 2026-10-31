"""The item categories and their restricted flags, read from category-taxonomy.md."""

import re
from pathlib import Path

from validator.data import TAXONOMY_PATH

# Matches a table row such as: | `alcohol` | yes | beer, wine, ... |
_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|\s*(yes|no)\s*\|")


def load_taxonomy(path: Path = TAXONOMY_PATH) -> dict[str, bool]:
    """Category name → whether it is restricted."""
    categories = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := _ROW.match(line):
            categories[match[1]] = match[2] == "yes"
    return categories
