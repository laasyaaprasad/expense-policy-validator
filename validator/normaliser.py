"""Deterministic cleanup of line text before translation and classification. No model calls.

Unicode NFKC folds full-width letters, digits and punctuation (common on Japanese receipts) into
their ordinary forms; whitespace is collapsed. Nothing else is removed: sizes and counts such as
"42ct" or "750ml" are evidence of what an item is, and the data has no SKU codes to strip. The
language comes from the data's `language` field, so nothing here detects it.
"""

import re
import unicodedata


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
