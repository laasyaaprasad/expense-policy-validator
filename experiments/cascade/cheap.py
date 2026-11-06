"""The cascade's cheap first stage: match each line to the nearest category.

Prototypes come from the classifier prompt's own category definitions and the hand-written dev set,
never from the receipts' or CORD's labels. A line's score for a category is its highest cosine
similarity to any of that category's prototypes; its margin is the best score minus the second best.
Two scorers share this: multilingual embeddings (meaning) and character n-gram TF-IDF (spelling).
"""

import re
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from validator.classifier import DEFINITIONS

# Splits a definition into the terms it lists: "beer, wine, spirits" -> "beer", "wine", "spirits".
_SPLIT = re.compile(r"[,;:()]|\bsuch as\b|\balso\b|\bincluding\b|\band\b|\bor\b")

# A line that seems to name a person goes to the LLM, which extracts the recipient. Written from the
# dev set's four recipient lines only ("invitée Dr. Lena Ortiz", "for Priya Nair", "for Tom Becker",
# "inspector D. Marsh"); misses on the receipts are measured, not fixed.
_NAME = r"[A-Z][\w'’-]+"
RECIPIENT = re.compile(
    r"\b(?:Dr|Mr|Mrs|Ms|Mx|Prof|Herr|Frau|Sr|Sra|Mme)\.?\s+[A-Z]"   # an honorific and a name
    rf"|\b[A-Z]\.\s?{_NAME}"                                         # an initial and a surname: D. Marsh
    rf"|\b(?:for|pour|para|für)\s+{_NAME}\s+{_NAME}"                 # for Priya Nair
    r"|(?i:\b(?:guest|invit\w*|inspector)\b)"                        # a word that introduces one
)


@dataclass(frozen=True)
class Prototype:
    text: str
    category: str
    source: str  # "definition", "term", or the dev line ID it came from


@dataclass(frozen=True)
class CheapLabel:
    category: str
    margin: float
    runner_up: str


def definition_prototypes() -> list[Prototype]:
    """Each category's definition as a sentence, and each term it lists."""
    seen, out = set(), []
    for category, text in DEFINITIONS.items():
        candidates = [(f"{category.replace('_', ' ')}: {text}", "definition")]
        candidates += [(term.strip(" ."), "term") for term in _SPLIT.split(text)]
        for term, source in candidates:
            # "the three item kinds below" and "none of the above" mean nothing without the prompt.
            if len(term) < 3 or "above" in term or "below" in term or (term, category) in seen:
                continue
            seen.add((term, category))
            out.append(Prototype(term, category, source))
    return out


def nearest(sims: np.ndarray, categories: list[str], exclude: np.ndarray | None = None) -> list[CheapLabel]:
    """One label per row of `sims` (lines x prototypes). `exclude` masks prototypes per line, so a dev
    item is never matched against itself (leave-one-out)."""
    sims = np.array(sims, dtype=float)
    if exclude is not None:
        sims[exclude] = -np.inf
    names = sorted(set(categories))
    columns = {name: [i for i, c in enumerate(categories) if c == name] for name in names}
    per_category = np.stack([sims[:, columns[name]].max(axis=1) for name in names], axis=1)
    labels = []
    for row in per_category:
        first, second = np.argsort(-row, kind="stable")[:2]
        labels.append(CheapLabel(names[first], float(row[first] - row[second]), names[second]))
    return labels


def embedding_sims(line_vectors: np.ndarray, prototype_vectors: np.ndarray) -> np.ndarray:
    return line_vectors @ prototype_vectors.T  # unit vectors, so this is cosine similarity


class Tfidf:
    """Character n-grams within words, fitted on the prototypes only."""

    def __init__(self, prototypes: list[Prototype]):
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True, lowercase=True)
        self.matrix = self.vectorizer.fit_transform([p.text for p in prototypes])

    def sims(self, texts: list[str]) -> np.ndarray:
        return (self.vectorizer.transform(texts) @ self.matrix.T).toarray()
