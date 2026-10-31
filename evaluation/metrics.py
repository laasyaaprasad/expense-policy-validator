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


# --- receipts -------------------------------------------------------------------------------------

DISPOSITIONS = ("flag", "review", "pass")


def confusion(gold: list[str], ours: list[str]) -> list[list[int]]:
    """Rows are the correct disposition, columns ours, both in DISPOSITIONS order."""
    return [[sum(g == row and o == col for g, o in zip(gold, ours)) for col in DISPOSITIONS] for row in DISPOSITIONS]


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n. Behaves sensibly at 0/n and n/n, where the
    normal approximation gives a zero-width interval."""
    from statsmodels.stats.proportion import proportion_confint
    low, high = proportion_confint(k, n, alpha=0.05, method="wilson")
    return round(float(low), 10), round(float(high), 10)  # float error leaves e.g. 2.8e-17 at 0/n


def false_positives(gold: list[str], ours: list[str]) -> tuple[int, int]:
    """Of the receipts that should pass, how many we flagged or sent to review."""
    return sum(g == "pass" and o != "pass" for g, o in zip(gold, ours)), gold.count("pass")


def missed_violations(gold: list[str], ours: list[str]) -> tuple[int, int]:
    """Of the receipts that should be flagged, how many we passed or downgraded to review."""
    return sum(g == "flag" and o != "flag" for g, o in zip(gold, ours)), gold.count("flag")


@dataclass(frozen=True)
class RuleScore:
    rule_id: str
    gold: int  # receipts where the answer key raises it
    raised: int  # receipts where we raise it
    matched: int  # receipts where both do (receipt + rule_id)
    evidence_correct: int  # matched, with exactly the same line IDs
    precision: float | None  # None ("n/a") when we never raise it
    recall: float | None  # None when the answer key never raises it


def rule_scores(gold: dict[str, dict[str, tuple[str, ...]]], ours: dict[str, dict[str, tuple[str, ...]]]) -> list[RuleScore]:
    """Per-rule matching, as the data dictionary defines it. Both arguments map receipt ID → rule ID →
    the line IDs cited (empty for whole-receipt rules and signals)."""
    rules = sorted({rule for rid in gold for rule in gold[rid]} | {rule for rid in ours for rule in ours[rid]})
    scores = []
    for rule in rules:
        in_gold = {rid for rid in gold if rule in gold[rid]}
        raised = {rid for rid in ours if rule in ours[rid]}
        matched = in_gold & raised
        scores.append(RuleScore(
            rule_id=rule, gold=len(in_gold), raised=len(raised), matched=len(matched),
            evidence_correct=sum(sorted(gold[rid][rule]) == sorted(ours[rid][rule]) for rid in matched),
            precision=len(matched) / len(raised) if raised else None,
            recall=len(matched) / len(in_gold) if in_gold else None,
        ))
    return scores
