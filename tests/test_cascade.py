"""The cascade experiment's logic (experiments/cascade), offline: no key, no model calls."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from experiments.cascade.cascade import choose_threshold, escalation_reason, fit_call_tokens
from experiments.cascade.cheap import RECIPIENT, CheapLabel, definition_prototypes, nearest
from experiments.cascade.embed import Embedder, EmbeddingError


def test_nearest_takes_the_best_category_and_its_margin():
    sims = np.array([[0.9, 0.7, 0.2], [0.1, 0.5, 0.45]])
    a, b = nearest(sims, ["alcohol", "food_beverage", "alcohol"])
    assert (a.category, a.runner_up, round(a.margin, 6)) == ("alcohol", "food_beverage", 0.2)
    assert (b.category, round(b.margin, 6)) == ("food_beverage", 0.05)


def test_leave_one_out_ignores_the_line_s_own_prototype():
    sims = np.array([[1.0, 0.6, 0.5]])
    (label,) = nearest(sims, ["alcohol", "food_beverage", "alcohol"], exclude=np.array([[True, False, False]]))
    assert label.category == "food_beverage"


def test_prototypes_split_definitions_into_terms():
    protos = {(p.text, p.category) for p in definition_prototypes()}
    assert ("beer", "alcohol") in protos and ("dishes cooked with alcohol", "food_beverage") in protos
    assert not any("above" in text or "below" in text for text, _ in protos)


@pytest.mark.parametrize("text", ["Déjeuner — invitée Dr. Lena Ortiz, Brightwave Capital", "Bouquet for Priya Nair, Halden Robotics",
                                  "Retirement gift for Tom Becker (our accounts team)", "Lunch for inspector D. Marsh, County Health Department"])
def test_the_recipient_gate_catches_the_dev_set_s_recipient_lines(text):
    assert RECIPIENT.search(text)


@pytest.mark.parametrize("text", ["Margherita pizza", "Dinner for two", "USB-C cable 2m", "Vino tinto Rioja Reserva 750ml"])
def test_the_recipient_gate_leaves_ordinary_lines_alone(text):
    assert not RECIPIENT.search(text)


def test_escalation_reasons():
    sure_food, sure_beer = CheapLabel("food_beverage", 0.3, "alcohol"), CheapLabel("alcohol", 0.3, "food_beverage")
    assert escalation_reason("Fish", sure_food, 0.1, conservative=True) is None
    assert escalation_reason("Fish", CheapLabel("food_beverage", 0.05, "alcohol"), 0.1, conservative=False) == "low margin"
    assert escalation_reason("Mug", CheapLabel("other", 0.9, "gift"), 0.1, conservative=False) == "cheap stage says other"
    assert escalation_reason("Beer", sure_beer, 0.1, conservative=False) is None
    assert escalation_reason("Beer", sure_beer, 0.1, conservative=True) == "restricted"
    assert escalation_reason("Gift for Tom Becker", sure_food, 0.1, conservative=False) == "names a person"


def test_threshold_rule_takes_the_lowest_qualifying_candidate():
    dev = [("a", CheapLabel("food_beverage", 0.30, "x"), "food_beverage"),
           ("b", CheapLabel("food_beverage", 0.20, "x"), "food_beverage"),
           ("c", CheapLabel("food_beverage", 0.05, "x"), "alcohol")]  # a wrong restricted flag at a low margin
    threshold, _ = choose_threshold(dev, conservative=False)
    assert threshold == 0.20  # keeps a and b, escalates c


def test_threshold_rule_escalates_everything_when_nothing_qualifies():
    dev = [("a", CheapLabel("food_beverage", 0.9, "x"), "alcohol")]
    assert choose_threshold(dev, conservative=False)[0] == math.inf


def test_call_tokens_fit_a_fixed_part_and_a_part_per_line():
    calls = [(n, 1000 + 20 * n, 10 + 50 * n) for n in (1, 2, 3, 4)]
    fit = fit_call_tokens(calls)
    assert round(fit.fixed_in) == 1000 and round(fit.per_line_in) == 20 and round(fit.per_line_out) == 50


class FakeEmbeddings:
    def __init__(self):
        self.calls = 0

    def create(self, model, input, extra_body):
        self.calls += 1
        data = [SimpleNamespace(index=i, embedding=[float(len(t)), 1.0, 0.0]) for i, t in enumerate(input)]
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": len(input), "cost": 1e-9 * len(input)})
        return SimpleNamespace(data=data, usage=usage, model=model, provider="DeepInfra")


def test_embeddings_are_cached_and_logged(tmp_path):
    fake = FakeEmbeddings()
    client = SimpleNamespace(embeddings=fake)
    embedder = Embedder(cache_path=tmp_path / "e.npz", ledger_path=tmp_path / "spend.jsonl", client=client)
    first = embedder.embed(["beer", "wine", "beer"])
    assert first.shape == (3, 3) and np.allclose(np.linalg.norm(first, axis=1), 1)
    again = Embedder(cache_path=tmp_path / "e.npz", ledger_path=tmp_path / "spend.jsonl", client=client).embed(["wine"])
    assert fake.calls == 1 and np.allclose(again[0], first[1])
    assert len((tmp_path / "spend.jsonl").read_text().splitlines()) == 1


def test_a_cache_miss_without_a_key_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("experiments.cascade.embed.load_dotenv", lambda *a, **k: None)
    with pytest.raises(EmbeddingError):
        Embedder(cache_path=tmp_path / "e.npz", ledger_path=tmp_path / "spend.jsonl").embed(["beer"])
