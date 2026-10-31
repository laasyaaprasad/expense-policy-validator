"""EXC-01: a receipt's disposition is computed from its exceptions and review signals, never judged."""

from collections.abc import Iterable

from validator.models import Disposition

FLAG_SEVERITIES = {"critical", "high", "medium"}
SEVERITIES = FLAG_SEVERITIES | {"low"}


def disposition(severities: Iterable[str], has_signals: bool) -> Disposition:
    severities = set(severities)
    if unknown := severities - SEVERITIES:
        raise ValueError(f"unknown severity {sorted(unknown)}")
    if severities & FLAG_SEVERITIES:
        return "flag"
    if severities or has_signals:
        return "review"
    return "pass"
