"""The semantic lookup that runs before the model (experiments 6–8; DECISIONS.md, section 12).

For each line of an itemized receipt, in order:
0. Code checks send the line to the model: a possible recipient, a phone, prescription or event ticket, no real
   words, or a merchant where context decides (package store, pharmacy, supermarket).
1a. Memory: a near-repeat of a reviewed line (similarity ≥ tau_mem) takes its category, if that category is
    unrestricted and no restricted reference is as close.
1b. The curated library may decide an unrestricted category the model isn't needed for, only if the line is
    familiar (≥ tau_fam), clear (margin ≥ safe_cutoff) and far from every restricted reference (gap ≥ delta).
Everything else goes to the model (validator/classifier.py), and the model's unsure lines to a human (EXC-03).
The lookup never decides a restricted category.

The embeddings come from a local open model (rules/lookup/config.yaml), cached by text in rules/lookup/vectors.npz.
Without the model installed (`uv sync --group semantic`), a line whose vector isn't cached goes to the model.
"""

import csv
import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from validator.data import ROOT, ReceiptInput
from validator.normaliser import normalise
from validator.taxonomy import load_taxonomy

LOOKUP_DIR = ROOT / "rules" / "lookup"
TAXONOMY = load_taxonomy()
ALWAYS_MODEL = frozenset({"other", "health_otc", "gift"})  # need an explanation, an item kind or a recipient
CONTEXT_MCCS = {"5921": "a package store (ALC-03)", "5912": "a pharmacy (prescription or not)",
                "5411": "a supermarket (cooking wine, household stock)"}

NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Mx|Dr|Prof|Sr|Sra|Mme|Herr|Frau)\.?\s+[A-Z]|\b[A-Z]\.\s?[A-Z][a-z]+|\bfor\s+[A-Z][a-z]|様|さん")
RECIPIENT_WORDS = re.compile(
    r"\b(?:gifts?|present|clients?|cadeaux?|regalos?|presente|geschenke?|kunden?|cliente?s?)\b|ギフト|プレゼント|贈答|お土産", re.I)
ITEM_KIND_WORDS = re.compile(
    r"\b(?:phones?|smartphones?|handsets?|iphone|cellphone|t[ée]l[ée]phone|tel[ée]fono|handy|telefone|celular|m[óo]vil"
    r"|prescriptions?|rx|rezept\w*|ordonnance|receta|receita|golf|concerts?|konzert|concierto|theat(?:er|re)s?|th[ée][âa]tre"
    r"|teatro|stadium|stade|estadio|match|admission|eintritt|entradas?|spectacle|opera|ballet|festival)\b"
    r"|携帯|スマホ|処方|コンサート|試合|ゴルフ|劇場", re.I)
# "tiket" (Indonesian) added after experiment 8, where a cinema ticket reached the library as airfare.
TICKET = re.compile(r"\b(?:tickets?|tikets?|billets?|bilhetes?|ingressos?)\b|チケット", re.I)
TRANSPORT = re.compile(r"\b(?:flights?|airline|airfare|plane|avion|flug\w*|vuelo|voo|train|rail|bahn|tren|trem|bus|metro|subway"
                       r"|ferry|tram|fare|pesawat|kereta)\b|航空|電車|新幹線", re.I)
WORD = re.compile(r"[^\W\d_]{3,}|[぀-ヿ㐀-鿿]")


LETTERS = re.compile(r"[^\W\d_]+")


def words(text: str) -> frozenset[str]:
    return frozenset(LETTERS.findall(normalise(text).lower()))


def same_words(a: str, b: str, overlap: float) -> bool:
    """The word test for "the same item" (experiment 9): Jaccard of at least `overlap`, or a set of two or more words
    inside the other. A single shared word ("*Rhum" in "BANANA RHUM RAISIN") isn't enough."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    small, big = sorted((wa, wb), key=len)
    return (len(small) >= 2 and small <= big) or len(wa & wb) / len(wa | wb) >= overlap


def code_check(text: str, mcc: str | None) -> str | None:
    """Step 0: why the line must go to the model before any lookup, or None."""
    t = normalise(text)
    if NAME.search(t) or RECIPIENT_WORDS.search(t):
        return "may name a recipient"
    if ITEM_KIND_WORDS.search(t) or (TICKET.search(t) and not TRANSPORT.search(t)):
        return "may be a phone, prescription or event ticket"
    if not WORD.search(t):
        return "no real words (a number, code or size)"
    if mcc in CONTEXT_MCCS:
        return f"the merchant is {CONTEXT_MCCS[mcc]}, where context decides"
    return None


@dataclass(frozen=True)
class LineDecision:
    decided: bool
    step: str  # "code check", "memory", "library" or "model"
    category: str | None  # set when decided
    confidence: float | None
    why: str
    nearest: tuple[tuple[str, str, float], ...] = ()  # (text, category, similarity): best match, then nearest restricted reference

    def trace(self) -> dict:
        return {"decided_by": f"lookup ({self.step})" if self.decided else ("model (code check)" if self.step == "code check" else "model"),
                "why": self.why, "nearest": [list(n) for n in self.nearest]}

    @property
    def reason(self) -> str:
        best = self.nearest[0] if self.nearest else ("", "", 0.0)
        source = "a reviewed line" if self.step == "memory" else "the curated library"
        return f"Semantic lookup: matches {source}, \"{best[0]}\" ({best[1]}, similarity {best[2]:.3f})."


class Embedder:
    """The local model, pinned, in float32; vectors cached by text so runs replay without it."""

    def __init__(self, model: dict, cache_path: Path):
        self.model, self.cache_path, self._loaded = model, cache_path, None
        self._cache: dict[str, np.ndarray] = {}
        if cache_path.exists():
            data = np.load(cache_path)
            self._cache = dict(zip(data["keys"].tolist(), data["vectors"]))

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model['name']}@{self.model['revision']}\n{self.model['instruction']}{text}".encode()).hexdigest()[:24]

    def available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False

    def embed(self, texts: list[str]) -> list[np.ndarray | None]:
        """One unit vector per text, or None where it isn't cached and the model isn't installed."""
        missing = list(dict.fromkeys(t for t in texts if self.key(t) not in self._cache))
        if missing and self.available():
            if self._loaded is None:
                import torch
                from sentence_transformers import SentenceTransformer
                self._loaded = SentenceTransformer(self.model["name"], revision=self.model["revision"], device="cpu",
                                                   model_kwargs={"dtype": torch.float32})
            vectors = self._loaded.encode([self.model["instruction"] + t for t in missing], batch_size=64, normalize_embeddings=True,
                                          convert_to_numpy=True, show_progress_bar=False)
            for text, v in zip(missing, vectors):
                self._cache[self.key(text)] = np.asarray(v, dtype=np.float32)
            self.save()
        return [self._cache.get(self.key(t)) for t in texts]

    def save(self) -> None:
        keys = sorted(self._cache)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.cache_path, keys=np.array(keys), vectors=np.stack([self._cache[k] for k in keys]))


class Lookup:
    def __init__(self, directory: Path = LOOKUP_DIR, overrides: dict | None = None):
        self.config = {**yaml.safe_load((directory / "config.yaml").read_text(encoding="utf-8")), **(overrides or {})}
        self.embedder = Embedder(self.config["model"], directory / "vectors.npz")
        with (directory / "library.csv").open(encoding="utf-8", newline="") as f:
            self.library = [(row["text"], row["category"]) for row in csv.DictReader(f)]
        with (directory / "memory.csv").open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.memory = [(r["text"], r["category"]) for r in rows if r["role"] == "remember"]
        self.risk = [(r["text"], r["category"]) for r in rows if r["role"] == "risk"]
        self.ready = False

    def _load_vectors(self) -> None:
        if self.ready:
            return
        vectors = self.embedder.embed([normalise(t) for t, _ in self.library + self.memory + self.risk])
        if any(v is None for v in vectors):
            self.ready = None  # the library itself can't be embedded: every line goes to the model
            return
        n_lib, n_mem = len(self.library), len(self.memory)
        self.lib_vectors = np.stack(vectors[:n_lib])
        self.mem_vectors = np.stack(vectors[n_lib:n_lib + n_mem]) if self.memory else np.zeros((0, self.lib_vectors.shape[1]), np.float32)
        restricted = [v for (t, c), v in zip(self.library, vectors[:n_lib]) if TAXONOMY[c]] + list(vectors[n_lib + n_mem:])
        self.restricted_refs = [(t, c) for t, c in self.library if TAXONOMY[c]] + self.risk
        self.restricted_vectors = np.stack(restricted)
        self.columns = {c: np.array([i for i, (_, cat) in enumerate(self.library) if cat == c]) for c in sorted({c for _, c in self.library})}
        self.ready = True

    def decide_receipt(self, receipt: ReceiptInput) -> dict[str, LineDecision]:
        """A decision for every line of an itemized receipt (an empty dict otherwise)."""
        if not receipt.itemized:
            return {}
        self._load_vectors()
        vectors = self.embedder.embed([normalise(line.description_raw) for line in receipt.line_items]) if self.ready else [None] * len(receipt.line_items)
        return {line.line_id: self._decide(line.description_raw, v, receipt.mcc) for line, v in zip(receipt.line_items, vectors)}

    def _decide(self, text: str, v: np.ndarray | None, mcc: str | None) -> LineDecision:
        c = self.config
        if (reason := code_check(text, mcc)) is not None:
            return LineDecision(False, "code check", None, None, reason)
        if v is None:
            return LineDecision(False, "model", None, None, "no embedding (the local model isn't installed): sent to the model")
        risk_sims = self.restricted_vectors @ v
        r = int(np.argmax(risk_sims))
        risk = (*self.restricted_refs[r], round(float(risk_sims[r]), 4))
        if len(self.memory):
            sims = self.mem_vectors @ v
            if c.get("memory_rule") == "same_item":  # experiment 9: close in meaning and sharing the words
                tau = c["tau_same"]
                candidates = [k for k in np.argsort(-sims) if sims[k] >= tau and same_words(text, self.memory[k][0], c["word_overlap"])]
                j = int(candidates[0]) if candidates else int(np.argmax(sims))
                hit = bool(candidates)
            else:
                tau, j = c["tau_mem"], int(np.argmax(sims))
                hit = sims[j] >= tau
            if hit:
                near = ((*self.memory[j], round(float(sims[j]), 4)), risk)
                if risk_sims[r] >= tau:
                    return LineDecision(False, "model", None, None, "a restricted reference is as close as the reviewed line", near)
                what = "the same item as a reviewed line" if c.get("memory_rule") == "same_item" else "close to a reviewed line"
                return LineDecision(True, "memory", self.memory[j][1], c["memory_confidence"], f"{what} (similarity {sims[j]:.3f}, threshold {tau:.3f})", near)
        lib_sims = self.lib_vectors @ v
        scores = {cat: float(lib_sims[cols].max()) for cat, cols in self.columns.items()}
        best, second = sorted(scores, key=scores.get, reverse=True)[:2]
        top = int(np.argmax(lib_sims))
        near = ((*self.library[top], round(float(lib_sims[top]), 4)), risk)
        if TAXONOMY[best]:
            return LineDecision(False, "model", None, None, f"closest to a restricted category ({best}), which only the model decides", near)
        if best in ALWAYS_MODEL:
            return LineDecision(False, "model", None, None, f"closest to {best}, which needs the model", near)
        if scores[best] < c["tau_fam"]:
            return LineDecision(False, "model", None, None, f"unfamiliar (best similarity {scores[best]:.3f})", near)
        if scores[best] - scores[second] < c["safe_cutoff"]:
            return LineDecision(False, "model", None, None, f"unclear between {best} and {second}", near)
        if scores[best] - risk_sims[r] < c["delta"]:
            return LineDecision(False, "model", None, None, f"too close to a restricted line ({risk[0]}, {risk[1]})", near)
        return LineDecision(True, "library", best, c["curated_confidence"], "familiar, clear and far from restricted lines", near)


    def learn(self, text: str, label: str, sure: bool, model_category: str | None, model_confidence: float | None) -> str:
        """After a person reviews a line: remember it (two keys), keep it as a risk reference (restricted), or leave it out."""
        self._load_vectors()
        text = normalise(text)
        if not sure:
            return "left out: unsure label"
        if not TAXONOMY[label] and (model_category != label or (model_confidence or 0) < self.config["two_key_confidence"]):
            return "left out: the model disagrees or isn't sure (two keys)"
        if any(t == text for t, _ in self.memory + self.risk):
            return "left out: already known"
        [v] = self.embedder.embed([text])
        if v is None:
            return "left out: no embedding"
        if TAXONOMY[label]:
            self.risk.append((text, label))
            self.restricted_refs.append((text, label))
            self.restricted_vectors = np.vstack([self.restricted_vectors, v])
            return "risk"
        self.memory.append((text, label))
        self.mem_vectors = np.vstack([self.mem_vectors, v])
        return "memory"


@lru_cache(maxsize=1)
def default_lookup() -> Lookup:
    return Lookup()
