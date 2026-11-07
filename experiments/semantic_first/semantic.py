"""The semantic stage (PLAN.md): decide a line from its nearest anchors when the answer is clear and safe,
and send everything else to the model.

Everything here is fixed by the plan: the anchors, the decontamination, the gates, the restricted
guard, and the rules that choose the cutoff and the confidence on the anchors alone.
"""

import csv
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from validator.normaliser import normalise
from validator.taxonomy import load_taxonomy

HERE = Path(__file__).resolve().parent
ANCHORS_PATH = HERE / "anchors.csv"
TAXONOMY = load_taxonomy()
ALWAYS_MODEL = frozenset({"other", "health_otc", "gift"})  # need an item kind, an explanation or a recipient
GUARD_K = 10  # the nearest anchors that must agree on the restricted flag
MIN_ACCURACY = 0.99  # of the anchors decided leave-one-out, at the chosen cutoff
JACCARD, RATIO = 0.5, 0.8  # near-copy limits for decontamination

# Text gates: what only the model can return. Names are matched case-sensitively, words without case.
NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Mx|Dr|Prof|Sr|Sra|Mme|Herr|Frau)\.?\s+[A-Z]|\b[A-Z]\.\s?[A-Z][a-z]+|\bfor\s+[A-Z][a-z]|様|さん")
RECIPIENT_WORDS = re.compile(
    r"\b(?:gifts?|present|clients?|cadeaux?|regalos?|presente|geschenke?|kunden?|cliente?s?)\b|ギフト|プレゼント|贈答|お土産", re.I)
ITEM_KIND_WORDS = re.compile(
    r"\b(?:phones?|smartphones?|handsets?|iphone|cellphone|t[ée]l[ée]phone|tel[ée]fono|handy|telefone|celular|m[óo]vil"
    r"|prescriptions?|rx|rezept\w*|ordonnance|receta|receita|golf|concerts?|konzert|concierto|theat(?:er|re)s?|th[ée][âa]tre"
    r"|teatro|stadium|stade|estadio|match|admission|eintritt|entradas?|spectacle|opera|ballet|festival)\b"
    r"|携帯|スマホ|処方|コンサート|試合|ゴルフ|劇場", re.I)
TICKET = re.compile(r"\b(?:tickets?|billets?|bilhetes?|ingressos?)\b|チケット", re.I)
TRANSPORT = re.compile(r"\b(?:flights?|airline|airfare|plane|avion|flug\w*|vuelo|voo|train|rail|bahn|tren|trem|bus|metro|subway"
                       r"|ferry|tram|fare)\b|航空|電車|新幹線", re.I)


@dataclass(frozen=True)
class Anchor:
    category: str
    language: str
    text: str
    source: str


def load_anchors(path: Path = ANCHORS_PATH) -> list[Anchor]:
    with path.open(encoding="utf-8", newline="") as f:
        return [Anchor(**row) for row in csv.DictReader(f)]


# --- decontamination -----------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalise(text).lower()))


def near_copy(a: str, b: str) -> bool:
    """The same normalised text, a word overlap of at least JACCARD, or a character similarity of at least RATIO."""
    na, nb = normalise(a).lower(), normalise(b).lower()
    if na == nb:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and len(ta & tb) / len(ta | tb) >= JACCARD:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= RATIO


def decontaminate(anchors: list[Anchor], test_texts: list[str]) -> tuple[list[Anchor], list[dict]]:
    """Anchors with no near-copy among the test lines, and the dropped ones with the line each copied."""
    kept, dropped = [], []
    tests = sorted(set(test_texts))
    for anchor in anchors:
        hit = next((t for t in tests if near_copy(anchor.text, t)), None)
        if hit is None:
            kept.append(anchor)
        else:
            dropped.append({"anchor": anchor.text, "category": anchor.category, "test_line": hit})
    return kept, dropped


# --- gates ---------------------------------------------------------------------------------------

def gate(text: str) -> str | None:
    """Why the text must go to the model whatever its neighbours say, or None."""
    t = normalise(text)
    if NAME.search(t) or RECIPIENT_WORDS.search(t):
        return "text gate: may name a recipient"
    if ITEM_KIND_WORDS.search(t) or (TICKET.search(t) and not TRANSPORT.search(t)):
        return "text gate: may be a phone, prescription or event ticket"
    return None


# --- scoring -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """A line's best category before the cutoff is applied."""
    category: str
    margin: float
    blocked: str | None  # why it must go to the model whatever the margin, or None
    neighbours: tuple[tuple[str, str, float], ...]  # the three nearest anchors: text, category, similarity


@dataclass(frozen=True)
class Decision:
    category: str  # the semantic stage's best category, decided or not
    margin: float
    decided: bool
    why: str  # "decided", or why it went to the model
    confidence: float | None  # set only when decided
    neighbours: tuple[tuple[str, str, float], ...]

    @property
    def reason(self) -> str:
        near = "; ".join(f"{text} ({category}, {sim:.3f})" for text, category, sim in self.neighbours)
        return f"Semantic stage: nearest anchors {near}."


def nearest_anchor_accuracy(vectors: np.ndarray, anchors: list[Anchor]) -> float:
    """Leave-one-out: the share of anchors whose nearest other anchor has the same category (the model choice)."""
    sims = vectors @ vectors.T
    np.fill_diagonal(sims, -np.inf)
    nearest = sims.argmax(axis=1)
    return float(np.mean([anchors[j].category == anchors[i].category for i, j in enumerate(nearest)]))


class SemanticStage:
    def __init__(self, model_name: str, anchors: list[Anchor], vectors: np.ndarray):
        self.model_name, self.anchors, self.vectors = model_name, anchors, vectors
        self.categories = sorted({a.category for a in anchors})
        self._columns = {c: np.array([i for i, a in enumerate(anchors) if a.category == c]) for c in self.categories}
        self._fit()

    def _candidate(self, sims: np.ndarray, text: str) -> Candidate:
        scores = {c: float(sims[cols].max()) for c, cols in self._columns.items()}
        ranked = sorted(scores, key=scores.get, reverse=True)
        best, second = ranked[0], ranked[1]
        order = np.argsort(-sims)
        nearest = [self.anchors[i] for i in order[:GUARD_K]]
        blocked = gate(text)
        if blocked is None and best in ALWAYS_MODEL:
            blocked = f"{best} needs the model (item kind, explanation or recipient)"
        if blocked is None and any(TAXONOMY[a.category] != TAXONOMY[best] for a in nearest):
            blocked = "restricted guard: restricted and unrestricted anchors are both near"
        neighbours = tuple((self.anchors[i].text, self.anchors[i].category, round(float(sims[i]), 4)) for i in order[:3])
        return Candidate(best, scores[best] - scores[second], blocked, neighbours)

    def _fit(self) -> None:
        """Leave-one-out on the anchors: choose the cutoff and record what the confidence is computed from."""
        sims = self.vectors @ self.vectors.T
        np.fill_diagonal(sims, -np.inf)
        loo = [self._candidate(sims[i], a.text) for i, a in enumerate(self.anchors)]
        eligible = [(c.margin, c.category == a.category, TAXONOMY[c.category] == TAXONOMY[a.category])
                    for c, a in zip(loo, self.anchors) if c.blocked is None]
        self.loo_blocked = sum(c.blocked is not None for c in loo)
        self.cutoff = float("inf")
        for tau in sorted({0.0, *(m for m, _, _ in eligible)}):
            decided = [(m, right, flag_ok) for m, right, flag_ok in eligible if m >= tau]
            if decided and sum(r for _, r, _ in decided) / len(decided) >= MIN_ACCURACY and all(f for _, _, f in decided):
                self.cutoff = tau
                break
        self._calibration = sorted((m, right) for m, right, _ in eligible if m >= self.cutoff)
        self.loo_decided = len(self._calibration)
        self.loo_right = sum(r for _, r in self._calibration)

    def confidence(self, margin: float) -> float:
        """The best smoothed accuracy, (right + 1) / (decided + 2), among the anchors decided leave-one-out with a
        margin of at least t, over every t from the cutoff up to this margin. It never falls as the margin rises
        (amended before any result, PLAN.md)."""
        best = 0.0
        for t in [self.cutoff] + [m for m, _ in self._calibration if m <= margin]:
            band = [right for m, right in self._calibration if m >= t]
            best = max(best, (sum(band) + 1) / (len(band) + 2))
        return best

    def decide(self, texts: list[str], vectors: np.ndarray) -> list[Decision]:
        out = []
        for text, sims in zip(texts, vectors @ self.vectors.T):
            c = self._candidate(sims, text)
            if c.blocked is not None:
                out.append(Decision(c.category, c.margin, False, c.blocked, None, c.neighbours))
            elif c.margin < self.cutoff:
                out.append(Decision(c.category, c.margin, False, f"margin {c.margin:.4f} below the cutoff {self.cutoff:.4f}", None, c.neighbours))
            else:
                out.append(Decision(c.category, c.margin, True, "decided", round(self.confidence(c.margin), 6), c.neighbours))
        return out

    def summary(self) -> dict:
        return {"model": self.model_name, "anchors": len(self.anchors), "cutoff": self.cutoff,
                "loo": {"blocked_by_a_gate_or_guard": self.loo_blocked, "decided": self.loo_decided, "right": self.loo_right}}
