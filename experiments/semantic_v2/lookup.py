"""The semantic lookup (PLAN.md, steps 0 and 1): code checks, then the nearest library lines, with a separate,
stricter cutoff for restricted categories. Everything it doesn't decide goes to the model.

The library, decontamination and text gates are experiment 6's (`experiments/semantic_first/semantic.py`).
New here: the no-words and merchant checks, and a cutoff and confidence per kind (restricted or not).
"""

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from experiments.semantic_first.semantic import ALWAYS_MODEL, TAXONOMY, Anchor, gate, load_anchors
from validator.normaliser import normalise

HERE = Path(__file__).resolve().parent
LIBRARY_PATH = HERE / "library.csv"
GUARD_K = 10
SAFE_ACCURACY, RESTRICTED_ACCURACY = 0.99, 1.0
CONTEXT_MCCS = {"5921": "a package store (ALC-03)", "5912": "a pharmacy (prescription or not)",
                "5411": "a supermarket (cooking wine, household stock)"}
WORD = re.compile(r"[^\W\d_]{3,}|[぀-ヿ㐀-鿿]")  # three letters in a row, or any Japanese or Chinese character


def load_library(path: Path = LIBRARY_PATH) -> list[Anchor]:
    return load_anchors(path)


def code_check(text: str, mcc: str | None) -> str | None:
    """Step 0: why the line must go to the model before any lookup, or None."""
    if (reason := gate(text)) is not None:
        return reason
    if not WORD.search(normalise(text)):
        return "code check: no real words (a number, code or size)"
    if mcc in CONTEXT_MCCS:
        return f"code check: the merchant is {CONTEXT_MCCS[mcc]}, where context decides"
    return None


@dataclass(frozen=True)
class Decision:
    category: str  # the lookup's best category, decided or not
    margin: float
    decided: bool
    why: str  # "decided", or why it went to the model
    confidence: float | None  # only when decided
    neighbours: tuple[tuple[str, str, float], ...]  # the three nearest library lines: text, category, similarity

    @property
    def reason(self) -> str:
        near = "; ".join(f"{text} ({category}, {sim:.3f})" for text, category, sim in self.neighbours)
        return f"Semantic lookup: nearest library lines {near}."


def _kind(category: str) -> str:
    return "restricted" if TAXONOMY[category] else "safe"


class Lookup:
    def __init__(self, library: list[Anchor], vectors: np.ndarray):
        self.library, self.vectors = library, vectors
        self._columns = {c: np.array([i for i, a in enumerate(library) if a.category == c]) for c in sorted({a.category for a in library})}
        self._fit()

    def _candidate(self, sims: np.ndarray, text: str, mcc: str | None) -> tuple[str, float, str | None, tuple]:
        scores = {c: float(sims[cols].max()) for c, cols in self._columns.items()}
        best, second = sorted(scores, key=scores.get, reverse=True)[:2]
        order = np.argsort(-sims)
        neighbours = tuple((self.library[i].text, self.library[i].category, round(float(sims[i]), 4)) for i in order[:3])
        blocked = code_check(text, mcc)
        if blocked is None and best in ALWAYS_MODEL:
            blocked = f"{best} needs the model (item kind, explanation or recipient)"
        if blocked is None and any(TAXONOMY[self.library[i].category] != TAXONOMY[best] for i in order[:GUARD_K]):
            blocked = "restricted guard: restricted and unrestricted library lines are both near"
        return best, scores[best] - scores[second], blocked, neighbours

    def _fit(self) -> None:
        """Leave-one-out on the library: a cutoff and a calibration list for each kind."""
        sims = self.vectors @ self.vectors.T
        np.fill_diagonal(sims, -np.inf)
        eligible = {"safe": [], "restricted": []}
        for i, anchor in enumerate(self.library):
            best, margin, blocked, _ = self._candidate(sims[i], anchor.text, None)
            if blocked is None:
                eligible[_kind(best)].append((margin, best == anchor.category, TAXONOMY[best] == TAXONOMY[anchor.category]))
        self.cutoff = {}
        for kind, need, floor in (("safe", SAFE_ACCURACY, 0.0), ("restricted", RESTRICTED_ACCURACY, None)):
            floor = self.cutoff.get("safe", 0.0) if floor is None else floor
            self.cutoff[kind] = float("inf")
            for t in sorted({floor, *(m for m, _, _ in eligible[kind] if m >= floor)}):
                decided = [(right, flag) for m, right, flag in eligible[kind] if m >= t]
                if decided and sum(r for r, _ in decided) / len(decided) >= need and all(f for _, f in decided):
                    self.cutoff[kind] = t
                    break
        self._calibration = {kind: sorted((m, right) for m, right, _ in eligible[kind] if m >= self.cutoff[kind]) for kind in eligible}

    def confidence(self, kind: str, margin: float) -> float:
        """The best smoothed accuracy among this kind's leave-one-out decisions with a margin of at least t,
        over every t from the cutoff up to this margin: it never falls as the margin rises."""
        calibration, best = self._calibration[kind], 0.0
        for t in [self.cutoff[kind]] + [m for m, _ in calibration if m <= margin]:
            band = [right for m, right in calibration if m >= t]
            best = max(best, (sum(band) + 1) / (len(band) + 2))
        return best

    def decide(self, texts: list[str], vectors: np.ndarray, mccs: list[str | None]) -> list[Decision]:
        out = []
        for text, mcc, sims in zip(texts, mccs, vectors @ self.vectors.T):
            best, margin, blocked, neighbours = self._candidate(sims, text, mcc)
            kind = _kind(best)
            if blocked is not None:
                out.append(Decision(best, margin, False, blocked, None, neighbours))
            elif margin < self.cutoff[kind]:
                out.append(Decision(best, margin, False, f"margin {margin:.4f} below the {kind} cutoff {self.cutoff[kind]:.4f}", None, neighbours))
            else:
                out.append(Decision(best, margin, True, "decided", round(self.confidence(kind, margin), 6), neighbours))
        return out

    def summary(self) -> dict:
        return {"library": len(self.library), "cutoff": self.cutoff,
                "leave_one_out": {kind: {"decided": len(c), "right": sum(r for _, r in c)} for kind, c in self._calibration.items()}}
