"""Version 3 of the semi-deterministic classifier (PLAN.md): code checks, then memory (near-repeats of reviewed
lines), then careful generalisation from the curated library, with the model as fallback.

The lookup never decides a restricted category. Learned lines only recognise near-repeats; restricted
reviewed lines are kept only as risk references, which block.
"""

from dataclasses import dataclass, field

import numpy as np

from experiments.semantic_first.semantic import ALWAYS_MODEL, TAXONOMY, Anchor
from experiments.semantic_v2.lookup import code_check

TWO_KEY_CONFIDENCE = 0.9


@dataclass(frozen=True)
class Decision:
    category: str  # the lookup's answer when decided, otherwise its best guess (or "" when none)
    decided: bool
    step: str  # "code check", "memory", "curated" or "model"
    why: str
    confidence: float | None
    neighbours: tuple[tuple[str, str, float], ...] = ()

    @property
    def reason(self) -> str:
        near = "; ".join(f"{text} ({category}, {sim:.3f})" for text, category, sim in self.neighbours)
        return f"Semantic lookup, {self.step}: {near}."


@dataclass(frozen=True)
class Params:
    tau_mem: float  # near-repeat similarity for memory
    tau_fam: float  # familiarity: the least best-similarity for curated generalisation
    delta: float  # the least gap between the best curated similarity and the nearest restricted reference
    safe_cutoff: float  # the curated margin cutoff (experiment 7's, on the library leave-one-out)
    memory_confidence: float
    curated_confidence: float


@dataclass
class Classifier:
    curated: list[Anchor]
    curated_vectors: np.ndarray
    params: Params
    memory: list[tuple[str, str]] = field(default_factory=list)  # (text, category), unrestricted only
    memory_vectors: list[np.ndarray] = field(default_factory=list)
    risk: list[tuple[str, str]] = field(default_factory=list)  # restricted reviewed lines
    risk_vectors: list[np.ndarray] = field(default_factory=list)

    def __post_init__(self):
        self._columns = {c: np.array([i for i, a in enumerate(self.curated) if a.category == c]) for c in sorted({a.category for a in self.curated})}
        self._curated_restricted = np.array([i for i, a in enumerate(self.curated) if TAXONOMY[a.category]])

    def _nearest_restricted(self, vector: np.ndarray, curated_sims: np.ndarray) -> tuple[float, tuple[str, str]]:
        i = self._curated_restricted[np.argmax(curated_sims[self._curated_restricted])]
        best, ref = float(curated_sims[i]), (self.curated[i].text, self.curated[i].category)
        if self.risk:
            sims = np.stack(self.risk_vectors) @ vector
            j = int(np.argmax(sims))
            if sims[j] > best:
                best, ref = float(sims[j]), self.risk[j]
        return best, ref

    def decide(self, text: str, vector: np.ndarray, mcc: str | None) -> Decision:
        p = self.params
        if (reason := code_check(text, mcc)) is not None:
            return Decision("", False, "code check", reason, None)
        curated_sims = self.curated_vectors @ vector
        risk_sim, risk_ref = self._nearest_restricted(vector, curated_sims)
        # 1a: memory, near-repeats of reviewed lines only.
        if self.memory:
            sims = np.stack(self.memory_vectors) @ vector
            j = int(np.argmax(sims))
            if sims[j] >= p.tau_mem:
                near = ((*self.memory[j], round(float(sims[j]), 4)), (*risk_ref, round(risk_sim, 4)))
                if risk_sim >= p.tau_mem:
                    return Decision(self.memory[j][1], False, "model", "a restricted reference is also a near-repeat", None, near)
                return Decision(self.memory[j][1], True, "memory", "decided", p.memory_confidence, near)
        # 1b: careful generalisation from the curated library.
        scores = {c: float(curated_sims[cols].max()) for c, cols in self._columns.items()}
        best, second = sorted(scores, key=scores.get, reverse=True)[:2]
        top = int(np.argmax(curated_sims))
        near = ((self.curated[top].text, self.curated[top].category, round(float(curated_sims[top]), 4)), (*risk_ref, round(risk_sim, 4)))
        if TAXONOMY[best]:
            return Decision(best, False, "model", "restricted categories always go to the model", None, near)
        if best in ALWAYS_MODEL:
            return Decision(best, False, "model", f"{best} needs the model (item kind, explanation or recipient)", None, near)
        if scores[best] < p.tau_fam:
            return Decision(best, False, "model", f"unfamiliar: best similarity {scores[best]:.3f} below {p.tau_fam:.2f}", None, near)
        if scores[best] - scores[second] < p.safe_cutoff:
            return Decision(best, False, "model", f"unclear: margin {scores[best] - scores[second]:.3f} below {p.safe_cutoff:.3f}", None, near)
        if scores[best] - risk_sim < p.delta:
            return Decision(best, False, "model", f"near a restricted reference ({risk_ref[0]}, {risk_ref[1]})", None, near)
        return Decision(best, True, "curated", "decided", p.curated_confidence, near)

    def learn(self, text: str, vector: np.ndarray, label: str, sure: bool, model_category: str | None, model_confidence: float | None) -> str:
        """After review: what happens to this line ("risk", "memory" or why it was left out)."""
        if not sure:
            return "left out: unsure label"
        if TAXONOMY[label]:
            self.risk.append((text, label))
            self.risk_vectors.append(vector)
            return "risk"
        if model_category != label or (model_confidence or 0) < TWO_KEY_CONFIDENCE:
            return "left out: the model disagrees or isn't sure (two keys)"
        if any(t == text for t, _ in self.memory):
            return "left out: already remembered"
        self.memory.append((text, label))
        self.memory_vectors.append(vector)
        return "memory"
