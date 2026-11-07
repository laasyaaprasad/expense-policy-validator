"""Scoring a generated config: field by field, by the existing config tests, and end to end.

The engine and retriever are the submitted ones, unchanged, called with the generated config instead
of rules.yaml. Nothing here writes to rules/ or outputs/.
"""

import json
import re
from collections import Counter

from experiments import TEMPERATURE_1
from evaluation.blame import load_classified
from evaluation.evaluate import decision_records, receipt_matches
from evaluation.gold import in_scope
from evaluation.oracle import load_attributes, oracle_receipts
from validator.data import load_receipts
from validator.engine import evaluate
from validator.engine.rules import CHECKS
from validator.policies import Rule
from validator.retriever import retrieve
from validator.rules_config import RuleSpec, RulesConfig

# The fields the model fills (PLAN.md). `evidence` is scored as "verbatim", since more than one
# sentence can be a fair quote; `id`, `doc` and `tier` are not the model's.
FIELDS = ("kind", "severity", "scope", "params", "applies_to", "overrides", "line_overrides", "replaces", "requires_reference")


def plain(text: str) -> str:
    """As tests/test_rules_config.py: markdown marks removed, whitespace collapsed."""
    return " ".join(re.sub(r"[*`]", "", text).split())


def value(spec: RuleSpec, field: str):
    """A field in a comparable form: sets for lists, plain dicts for the rest."""
    v = getattr(spec, field)
    if field == "applies_to":
        return {k: (sorted(x) if isinstance(x, tuple) else x) for k, x in v.model_dump().items() if x not in ((), False)}
    if field in ("overrides", "line_overrides", "replaces"):
        return sorted(v)
    if field == "params":
        return {k: str(x) for k, x in sorted(v.items())}
    return v


def canonical(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def compare(generated: dict[str, RuleSpec], reference: RulesConfig, policies: dict[str, Rule]) -> dict:
    """Per field and per rule, against the hand-reviewed config."""
    per_field = {field: 0 for field in (*FIELDS, "evidence_verbatim")}
    rules, diffs = 0, {}
    for ref in reference.rules:
        spec = generated.get(ref.id)
        if spec is None:
            diffs[ref.id] = {"missing": True}
            continue
        wrong = {f: {"generated": value(spec, f), "reference": value(ref, f)} for f in FIELDS
                 if canonical(value(spec, f)) != canonical(value(ref, f))}
        verbatim = plain(spec.evidence) in plain(policies[ref.id].text)
        if not verbatim:
            wrong["evidence"] = {"generated": spec.evidence, "verbatim": False}
        for f in FIELDS:
            per_field[f] += f not in wrong
        per_field["evidence_verbatim"] += verbatim
        rules += not wrong
        if wrong:
            diffs[ref.id] = wrong
    n = len(reference.rules)
    return {"rules_exactly_right": {"k": rules, "n": n}, "fields": {f: {"k": k, "n": n} for f, k in per_field.items()},
            "diffs": diffs}


def build(generated: dict[str, RuleSpec], reference: RulesConfig) -> RulesConfig:
    """A full config: the generated rules, in rules.yaml's order, with the shared tables copied (PLAN.md)."""
    shared = reference.model_dump(exclude={"rules"})
    return RulesConfig(**shared, rules=tuple(generated[r.id] for r in reference.rules if r.id in generated))


def lenient(config: RulesConfig) -> tuple[RulesConfig, list[str]]:
    """PLAN.md's repair: a non-mechanic rule with no Python check becomes a mechanic, so the engine can run."""
    repaired, changed = [], []
    for rule in config.rules:
        if rule.kind != "mechanic" and rule.id not in CHECKS:
            rule = rule.model_copy(update={"kind": "mechanic", "severity": None, "scope": None, "tier": None})
            changed.append(rule.id)
        repaired.append(rule)
    return config.model_copy(update={"rules": tuple(repaired)}), changed


def config_tests(config: RulesConfig, policies: dict[str, Rule], gold) -> dict[str, str]:
    """The existing config tests (tests/test_rules_config.py), run on this config. "pass" or the failure."""
    import tests.test_rules_config as t
    cases = {
        "every rule encoded or out of scope": lambda: t.test_every_rule_is_encoded_or_out_of_scope(config, policies),
        "each rule names its document": lambda: t.test_each_rule_names_its_document(config, policies),
        "evidence is verbatim policy text": lambda: t.test_evidence_is_verbatim_policy_text(config, policies),
        "numeric params appear in the rule text": lambda: t.test_numeric_params_appear_in_rule_text(config, policies),
        "severity is stated in the rule text": lambda: t.test_severity_is_stated_in_rule_text(config, policies),
        "only HEALTH-01 has two severities": lambda: t.test_only_health_01_has_two_severities(config),
        "whole-receipt scope matches EXC-04": lambda: t.test_whole_receipt_scope_matches_exc_04(config, policies),
        "the review-signal rules": lambda: t.test_review_signal_rules(config),
        "overrides point at encoded rules": lambda: t.test_overrides_point_at_encoded_rules(config),
        "severity matches the answer key (reads the answer key)": lambda: t.test_severity_matches_gold(config, gold),
    }
    results = {}
    for name, run in cases.items():
        try:
            run()
            results[name] = "pass"
        except AssertionError as error:
            results[name] = f"fail: {str(error)[:300]}"
    return results


def decide(classified, config: RulesConfig, policies: dict[str, Rule], retrieval: bool):
    retrieved = {c.receipt.receipt_id: retrieve(c, config, policies).rule_ids for c in classified} if retrieval else None
    return decision_records(evaluate(classified, config, retrieved))


def end_to_end(config: RulesConfig, policies: dict[str, Rule], gold, cord_classified, cord_reference: dict[str, dict]) -> dict:
    """Oracle and system mode on the 28 receipts, and agreement with the reference config on CORD.
    A crash is reported, not raised."""
    out = {}
    runs = {
        "oracle": lambda: (oracle_receipts(load_receipts(), gold, load_attributes()), False),
        "system": lambda: (load_classified(TEMPERATURE_1 / "classifications.jsonl"), True),
    }
    for mode, inputs in runs.items():
        try:
            classified, retrieval = inputs()
            records = decide(classified, config, policies, retrieval)
            scored = sorted(rid for rid in records if in_scope(rid))
            wrong = [rid for rid in scored if not receipt_matches(records[rid], gold[rid])]
            out[mode] = {"exactly_right": {"k": len(scored) - len(wrong), "n": len(scored)}, "wrong": wrong}
        except Exception as error:  # noqa: BLE001 - a crash is a result here
            out[mode] = {"crash": f"{type(error).__name__}: {error}"[:300]}
    try:
        records = decide(cord_classified, config, policies, True)
        same = [rid for rid in cord_reference if _decision(records[rid]) == _decision(cord_reference[rid])]
        changed = sorted(set(cord_reference) - set(same))
        out["cord_agreement"] = {"k": len(same), "n": len(cord_reference), "changed": changed[:20],
                                 "dispositions": dict(Counter(records[rid]["disposition"] for rid in records))}
    except Exception as error:  # noqa: BLE001
        out["cord_agreement"] = {"crash": f"{type(error).__name__}: {error}"[:300]}
    return out


def _decision(record: dict) -> tuple:
    return (record["disposition"], tuple(sorted((e["rule_id"], tuple(sorted(e["line_ids"]))) for e in record["exceptions"])),
            tuple(sorted(s["rule_id"] for s in record["review_signals"])))


def majority(samples: list[RuleSpec]) -> tuple[RuleSpec, list[str]]:
    """Self-consistency: the majority value per field, and the fields where the samples disagree.
    Falls back to the first sample if the majority fields don't make a valid rule together."""
    flagged, update = [], {}
    for field in (*FIELDS, "evidence"):
        counts = Counter(canonical(value(s, field) if field != "evidence" else s.evidence) for s in samples)
        if len(counts) > 1:
            flagged.append(field)
        top, n = counts.most_common(1)[0]
        source = next(s for s in samples if canonical(value(s, field) if field != "evidence" else s.evidence) == top)
        update[field] = getattr(source, field) if n * 2 > len(samples) else getattr(samples[0], field)
    try:
        return RuleSpec.model_validate({**samples[0].model_dump(), **{k: v for k, v in update.items()}}), flagged
    except Exception:  # noqa: BLE001 - an inconsistent mix of majority fields
        return samples[0], flagged
