"""Metric code, kept apart from the scripts that print it so each can be checked on hand-worked cases.

Definitions follow the data dictionary and the brief: macro F1 averages over the categories in the
answers only (`labels=sorted(set(y_true))`), and a score with no predictions or no answers counts as 0.
"""

from dataclasses import dataclass

from sklearn.metrics import f1_score, precision_recall_fscore_support

from validator.engine.rules import _recipient_key


@dataclass(frozen=True)
class CategoryScore:
    category: str
    support: int  # items with this category in the answers
    predicted: int  # items the system put in it
    correct: int
    precision: float
    recall: float
    f1: float


def macro_f1(y_true: list[str], y_pred: list[str]) -> float:
    """A prediction outside the answer categories (say `gambling`) gets no score of its own; it only
    costs the true category its recall."""
    return float(f1_score(y_true, y_pred, labels=sorted(set(y_true)), average="macro", zero_division=0))


def per_category(y_true: list[str], y_pred: list[str]) -> list[CategoryScore]:
    labels = sorted(set(y_true))
    precision, recall, f1, support = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    return [
        CategoryScore(
            category=label, support=int(support[i]), predicted=sum(p == label for p in y_pred),
            correct=sum(t == p == label for t, p in zip(y_true, y_pred)),
            precision=float(precision[i]), recall=float(recall[i]), f1=float(f1[i]),
        )
        for i, label in enumerate(labels)
    ]


def binary_f1(y_true: list[bool], y_pred: list[bool]) -> float:
    """F1 with True as the positive class (restricted F1: a restricted item is the thing to find)."""
    return float(f1_score(y_true, y_pred, pos_label=True, zero_division=0))


def recipient_matches(got_name: str | None, got_type: str | None, want_name: str | None, want_type: str | None) -> bool:
    """Types must agree; names match when one contains the other once punctuation and titles are set aside."""
    if got_type != want_type:
        return False
    if want_name is None:
        return got_name is None
    return got_name is not None and (_recipient_key(want_name) in _recipient_key(got_name) or _recipient_key(got_name) in _recipient_key(want_name))
