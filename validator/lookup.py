"""The semantic lookup that runs before the model (experiments 6–12; DECISIONS.md, section 12).

The pipeline's rule (rules/lookup/config.yaml, `memory_rule: word_by_word`, experiment 12). For each line of an
itemized receipt, in order:
0. Code checks send the line to the model: a possible recipient, a phone, prescription or event ticket, no real
   words, or a merchant where context decides (package store, pharmacy, supermarket).
1. Restricted lines block: a restricted reference at sentence similarity ≥ block_tau, or one whose words match the
   line's by meaning, sends it to the model.
2. Every word accounted for, by meaning: a reviewed line decides only if each of its words matches a word of the
   line, and each word of the line matches one of its words, at word similarity ≥ tau_word.
Everything else goes to the model (validator/classifier.py), and the model's unsure lines to a human (EXC-03).
The lookup never decides a restricted category. With `mode: decide` (the pipeline's setting) a fixed share of its
decisions (`audit_rate`) is marked `audit` for a person to spot-check. `mode: shadow` is the kill switch: it decides
nothing, each would-be decision is kept in the trace as `shadow`, and the model decides the line. The lookup report
(evaluation/lookup_report.py) checks both. Memory grows only with two keys: a person's decision and the model
agreeing (`learn`), never from the model alone.

The earlier rules stay selectable for the experiments: V3's near-repeat memory and curated library (no
`memory_rule`), the same-item rule (`same_item`, experiments 9–11) and exact word sets (`exact_words`).

The embeddings come from a local open model (rules/lookup/config.yaml), cached by text in rules/lookup/vectors.npz.
Without the model installed (`uv sync --group semantic`), a line whose vector isn't cached goes to the model.
"""

import csv
import hashlib
import re
from dataclasses import dataclass, replace
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


UNITS = frozenset({"ml", "gr", "kg", "pcs", "pc", "pkt", "ltr", "oz", "cl"})


def item_words(text: str) -> tuple[str, ...]:
    """Experiment 12's words: runs of two or more letters, lower-cased, without units. Size codes ("[L]") drop out."""
    return tuple(sorted({w for w in LETTERS.findall(normalise(text).lower()) if len(w) >= 2 and w not in UNITS}))


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


def restricted_word(text: str):
    """Experiment 11: the first restricted term of the keyword baseline's frozen list (rules/keywords.yaml) in the line, or None."""
    from validator.baseline import load_terms, match
    hit = match(text, tuple(t for t in load_terms() if t.restricted))
    return hit


@dataclass(frozen=True)
class LineDecision:
    decided: bool
    step: str  # "code check", "memory", "library" or "model"
    category: str | None  # set when decided
    confidence: float | None
    why: str
    nearest: tuple[tuple[str, str, float], ...] = ()  # (text, category, similarity): best match, then nearest restricted reference
    shadow: str | None = None  # shadow mode: the category the lookup would have decided; the model decides the line
    audit: bool = False  # in the audit sample: a person should check this lookup decision (it stands meanwhile)

    def trace(self) -> dict:
        return {"decided_by": f"lookup ({self.step})" if self.decided else ("model (code check)" if self.step == "code check" else "model"),
                "why": self.why, "nearest": [list(n) for n in self.nearest], **({"shadow": self.shadow} if self.shadow else {}),
                **({"audit": True} if self.audit else {})}

    @property
    def reason(self) -> str:
        best = self.nearest[0] if self.nearest else ("", "", 0.0)
        source = "a reviewed line" if self.step == "memory" else "the curated library"
        return f"Semantic lookup: matches {source}, \"{best[0]}\" ({best[1]}, similarity {best[2]:.3f})."


def in_audit_sample(line_id: str, text: str, rate: float) -> bool:
    """A fixed, repeatable share of the lookup's decisions for a person to spot-check. Keyed on the line, not only the
    text, so a line that repeats across receipts is still sampled now and then."""
    return int(hashlib.sha256(f"{line_id}\n{text}".encode()).hexdigest()[:8], 16) / 2**32 < rate


def shadowed(d: LineDecision) -> LineDecision:
    """Shadow mode: the model decides the line; the lookup's answer is kept in the trace for the lookup report."""
    if not d.decided:
        return d
    return LineDecision(False, "shadow", None, None, f"shadow mode: would have decided {d.category} ({d.why})", d.nearest, shadow=d.category)


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


DEFAULT_CLIENT = "default"
MEMORY_FIELDS = ["role", "category", "text", "reviewed_line"]


def client_dir(client: str = DEFAULT_CLIENT, directory: Path = LOOKUP_DIR) -> Path:
    """Where a client's own memory and reviews live: the lookup folder itself for the default client, else
    clients/<name>/. The config, library and vectors are shared by every client."""
    if client == DEFAULT_CLIENT:
        return directory
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", client):
        raise ValueError(f"client names are lower-case letters, digits, '-' and '_': {client!r}")
    return directory / "clients" / client


def read_memory(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Lookup:
    def __init__(self, directory: Path = LOOKUP_DIR, overrides: dict | None = None, client: str = DEFAULT_CLIENT):
        self.config = {**yaml.safe_load((directory / "config.yaml").read_text(encoding="utf-8")), **(overrides or {})}
        self.embedder = Embedder(self.config["model"], directory / "vectors.npz")
        with (directory / "library.csv").open(encoding="utf-8", newline="") as f:
            self.library = [(row["text"], row["category"]) for row in csv.DictReader(f)]
        self.client = client
        self.memory_path = client_dir(client, directory) / "memory.csv"
        rows = read_memory(self.memory_path)
        self.memory = [(r["text"], r["category"]) for r in rows if r["role"] == "remember"]
        self.risk = [(r["text"], r["category"]) for r in rows if r["role"] == "risk"]
        # Restricted references are shared: a line any client's reviewer confirmed as restricted blocks for every client.
        # Remembered (safe) lines stay with their own client.
        others = [p for p in [directory / "memory.csv", *sorted((directory / "clients").glob("*/memory.csv"))] if p != self.memory_path]
        known = {t for t, _ in self.risk}
        for path in others:
            for r in read_memory(path):
                if r["role"] == "risk" and r["text"] not in known:
                    self.risk.append((r["text"], r["category"]))
                    known.add(r["text"])
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
        if self.config.get("memory_rule") == "word_by_word":
            self.prepare_words([t for t, _ in self.memory + self.restricted_refs])

    def decide_receipt(self, receipt: ReceiptInput) -> dict[str, LineDecision]:
        """A decision for every line of an itemized receipt (an empty dict otherwise)."""
        if not receipt.itemized:
            return {}
        self._load_vectors()
        vectors = self.embedder.embed([normalise(line.description_raw) for line in receipt.line_items]) if self.ready else [None] * len(receipt.line_items)
        decisions = {line.line_id: self._decide(line.description_raw, v, receipt.mcc) for line, v in zip(receipt.line_items, vectors)}
        if self.config.get("mode") == "shadow":
            return {k: shadowed(d) for k, d in decisions.items()}
        rate = self.config.get("audit_rate", 0.0)
        texts = {line.line_id: line.description_raw for line in receipt.line_items}
        return {k: replace(d, audit=True) if d.decided and in_audit_sample(k, texts[k], rate) else d for k, d in decisions.items()}

    def _decide(self, text: str, v: np.ndarray | None, mcc: str | None) -> LineDecision:
        c = self.config
        if (reason := code_check(text, mcc)) is not None:
            return LineDecision(False, "code check", None, None, reason)
        if c.get("restricted_words") and (term := restricted_word(text)) is not None:  # experiment 11
            return LineDecision(False, "code check", None, None, f'a restricted word ("{term.text}", {term.category})')
        if c.get("memory_rule") == "exact_words":  # experiment 12's non-semantic baseline
            return self._decide_exact(text)
        if v is None:
            return LineDecision(False, "model", None, None, "no embedding (the local model isn't installed): sent to the model")
        risk_sims = self.restricted_vectors @ v
        r = int(np.argmax(risk_sims))
        risk = (*self.restricted_refs[r], round(float(risk_sims[r]), 4))
        if c.get("memory_rule") == "word_by_word":
            return self._decide_words(text, v, risk_sims, risk)
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
                if risk_sims[r] >= c.get("block_tau", tau):
                    return LineDecision(False, "model", None, None, "a restricted reference is as close as the reviewed line", near)
                # Experiment 11: a restricted reference that is the same item blocks too, and the library must agree.
                if c.get("block_same_item"):
                    same = [k for k in np.argsort(-risk_sims) if risk_sims[k] >= tau and same_words(text, self.restricted_refs[k][0], c["word_overlap"])]
                    if same:
                        k = int(same[0])
                        return LineDecision(False, "model", None, None, "a restricted reference is the same item",
                                            (near[0], (*self.restricted_refs[k], round(float(risk_sims[k]), 4))))
                if c.get("library_agrees"):
                    scores = self._library_scores(v)
                    best = max(scores, key=scores.get)
                    if best != self.memory[j][1]:
                        return LineDecision(False, "model", None, None,
                                            f"the library's closest category ({best}) disagrees with the reviewed line's ({self.memory[j][1]})", near)
                what = "the same item as a reviewed line" if c.get("memory_rule") == "same_item" else "close to a reviewed line"
                return LineDecision(True, "memory", self.memory[j][1], c["memory_confidence"], f"{what} (similarity {sims[j]:.3f}, threshold {tau:.3f})", near)
        lib_sims = self.lib_vectors @ v
        scores = self._library_scores(v, lib_sims)
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

    # Experiment 12: every word accounted for, by meaning (experiments/semantic_v4/PLAN.md).
    def word_score(self, a: tuple[str, ...], b: tuple[str, ...]) -> float:
        """The highest τ_word at which a and b account for each other's words: every word on each side has a word on
        the other side at least that similar. 0 if either side has no words or a word can't be embedded."""
        if not a or not b:
            return 0.0
        va, vb = self._word_vectors(a), self._word_vectors(b)
        if va is None or vb is None:
            return 1.0 if a == b else 0.0
        s = va @ vb.T
        return float(min(s.max(axis=1).min(), s.max(axis=0).min()))

    def prepare_words(self, texts: list[str]) -> None:
        """Embed every word of these texts in one batch (the embedder saves its cache once per batch)."""
        self._word_vectors(tuple(dict.fromkeys(w for t in texts for w in item_words(t))))

    def _word_vectors(self, words: tuple[str, ...]) -> np.ndarray | None:
        cache = self.__dict__.setdefault("_wv", {})
        missing = [w for w in words if w not in cache]
        if missing:
            for w, vec in zip(missing, self.embedder.embed(missing)):
                cache[w] = vec
        vecs = [cache[w] for w in words]
        return None if any(x is None for x in vecs) else np.stack(vecs)

    def _decide_words(self, text: str, v: np.ndarray, risk_sims: np.ndarray, risk: tuple) -> LineDecision:
        c, words = self.config, item_words(text)
        if not words:
            return LineDecision(False, "model", None, None, "no words left once units and size codes are set aside")
        tau = c["tau_word"]
        if risk_sims.max() >= c["block_tau"]:
            return LineDecision(False, "model", None, None, f"close to a restricted line ({risk[0]}, {risk[1]}, similarity {risk[2]:.3f})", (risk, risk))
        if c.get("risk_words"):  # the reported-only arm: any word like a word of a reviewed restricted line
            risk_words = tuple(dict.fromkeys(w for t, _ in self.risk for w in item_words(t)))
            lv, rv = self._word_vectors(words), self._word_vectors(risk_words) if risk_words else None
            if lv is not None and rv is not None and (lv @ rv.T).max() >= tau:
                i, j = np.unravel_index(int(np.argmax(lv @ rv.T)), (len(words), len(risk_words)))
                return LineDecision(False, "model", None, None, f'a word like one in a reviewed restricted line ("{words[i]}" ~ "{risk_words[j]}")', (risk,))
        for k, (ref, cat) in enumerate(self.restricted_refs):
            if self.word_score(words, item_words(ref)) >= tau:
                hit = (ref, cat, round(float(risk_sims[k]), 4))
                return LineDecision(False, "model", None, None, f"has the same words as a restricted line ({ref}, {cat})", (hit, hit))
        if len(self.memory):
            sims = self.mem_vectors @ v
            for j in np.argsort(-sims)[:20]:
                score = self.word_score(words, item_words(self.memory[j][0]))
                if score >= tau:
                    near = ((*self.memory[j], round(float(sims[j]), 4)), risk)
                    return LineDecision(True, "memory", self.memory[j][1], c["memory_confidence"],
                                        f"every word matches a reviewed line (word similarity {score:.3f}, threshold {tau:.3f})", near)
        return LineDecision(False, "model", None, None, "no reviewed line has every word accounted for", (risk,) if not len(self.memory) else
                            ((*self.memory[int(np.argmax(sims))], round(float(sims.max()), 4)), risk))

    def _decide_exact(self, text: str) -> LineDecision:
        words = item_words(text)
        if not words:
            return LineDecision(False, "model", None, None, "no words left once units and size codes are set aside")
        for ref, cat in self.risk + [(t, c) for t, c in self.library if TAXONOMY[c]]:
            if item_words(ref) == words:
                return LineDecision(False, "model", None, None, f"the same words as a restricted line ({ref}, {cat})")
        for t, cat in self.memory:
            if item_words(t) == words:
                return LineDecision(True, "memory", cat, self.config["memory_confidence"], "the same words as a reviewed line", ((t, cat, 1.0),))
        return LineDecision(False, "model", None, None, "no reviewed line has the same words")

    def _library_scores(self, v: np.ndarray, lib_sims: np.ndarray | None = None) -> dict[str, float]:
        """Each library category's score: its best similarity to the line."""
        lib_sims = self.lib_vectors @ v if lib_sims is None else lib_sims
        return {cat: float(lib_sims[cols].max()) for cat, cols in self.columns.items()}


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

    def forget(self, text: str, keep: str) -> list[tuple[str, str]]:
        """A reviewer corrected a remembered line: drop every remembered line with the same words (case and size codes
        aside, so "Soup of the day" corrects "Soup Of The Day") whose category isn't `keep`."""
        self._load_vectors()
        words = item_words(text)
        drop = [i for i, (t, c) in enumerate(self.memory) if words and item_words(t) == words and c != keep]
        removed = [self.memory[i] for i in drop]
        if drop:
            self.memory = [m for i, m in enumerate(self.memory) if i not in set(drop)]
            if self.ready is True:
                self.mem_vectors = np.delete(self.mem_vectors, drop, axis=0)
        return removed


@lru_cache(maxsize=1)
def default_lookup() -> Lookup:
    return Lookup()
