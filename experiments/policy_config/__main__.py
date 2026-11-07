"""Run experiment 3: `uv run python -m experiments.policy_config` (or `make policy-config`).

Extracts every rule under the three variants in PLAN.md, builds a config from each, and scores it
against the hand-reviewed rules.yaml, by the existing config tests and end to end. The hand config is
scored the same way as a check on the scoring. Offline once cache/ holds every reply; a miss needs
OPENROUTER_API_KEY. Writes results/; never touches rules/ or outputs/.
"""

import json
import re
import sys
from pathlib import Path

import yaml

from experiments import TEMPERATURE_1
from evaluation import cord
from evaluation.gold import load_gold
from experiments.policy_config.extract import extract
from experiments.policy_config.score import build, compare, config_tests, decide, end_to_end, lenient, majority
from validator.data import load_receipts
from validator.llm import LLM, LLMError, ledger_total
from validator.policies import load_policies
from validator.rules_config import load_rules

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CACHE_DIR, LEDGER_PATH = HERE / "cache", HERE / "spend.jsonl"
SAMPLES = 3


def _llm() -> LLM:
    try:
        return LLM(cache_dir=CACHE_DIR, ledger_path=LEDGER_PATH)
    except LLMError as error:
        if not any((CACHE_DIR / "policy_config").glob("*.json")):
            # The cache is not committed (every prompt holds policy text), so with no key nothing can be replayed.
            # Stop rather than write empty results over the committed ones.
            raise SystemExit(f"{error}, and this experiment's cache is not committed: rerun it with a key. "
                             "Its committed results are unchanged.") from error
        print(f"{error}. Replaying the cache instead.")
        return LLM.offline(cache_dir=CACHE_DIR, ledger_path=LEDGER_PATH)


def _yaml(config) -> str:
    data = json.loads(config.model_dump_json(exclude_defaults=True))
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    llm, policies, reference, gold = _llm(), load_policies(), load_rules(), load_gold()
    interface = {r.id: {"parameter_names": sorted(r.params), "tier": r.tier,
                        "severity_cases": sorted(r.severity) if isinstance(r.severity, dict) else []} for r in reference.rules}

    def run(retrieval: bool, temperature: float, sample: str = ""):
        specs, failures = {}, {}
        for rule_id, iface in interface.items():
            try:
                specs[rule_id] = extract(llm, policies[rule_id], policies, reference, parameter_names=iface["parameter_names"],
                                         severity_cases=iface["severity_cases"], tier=iface["tier"], retrieval=retrieval,
                                         temperature=temperature, sample=sample)
            except LLMError as error:
                failures[rule_id] = str(error)[:300]
        return specs, failures

    variants, failures = {}, {}
    variants["plain"], failures["plain"] = run(retrieval=False, temperature=0)
    variants["retrieval"], failures["retrieval"] = run(retrieval=True, temperature=0)
    samples = [run(retrieval=True, temperature=1, sample=f"s{i}") for i in range(1, SAMPLES + 1)]
    voted, flags = {}, {}
    for rule_id in interface:
        got = [s[0][rule_id] for s in samples if rule_id in s[0]]
        if got:
            voted[rule_id], flags[rule_id] = majority(got)
    variants["self_consistency"] = voted
    failures["self_consistency"] = {k: v for s in samples for k, v in s[1].items()}

    cord_receipts = load_receipts(cord.RECEIPTS_PATH)
    cord_classified = cord.load_system(cord_receipts, TEMPERATURE_1 / "cord" / "classifications.jsonl")[0]
    cord_reference = decide(cord_classified, reference, policies, True)

    results = {"hand_rules_yaml": {"config_tests": config_tests(reference, policies, gold),
                                   "end_to_end": end_to_end(reference, policies, gold, cord_classified, cord_reference)}}
    for name, specs in variants.items():
        entry = {"extraction_failures": failures[name], "fields": compare(specs, reference, policies)}
        if len(specs) == len(interface):
            config = build(specs, reference)
            (RESULTS / f"rules_{name}.yaml").write_text(_yaml(config), encoding="utf-8")
            entry["config_tests"] = config_tests(config, policies, gold)
            entry["end_to_end"] = end_to_end(config, policies, gold, cord_classified, cord_reference)
            if any("crash" in v for v in entry["end_to_end"].values()):
                repaired, changed = lenient(config)
                entry["lenient"] = {"repaired_to_mechanic": changed,
                                    "end_to_end": end_to_end(repaired, policies, gold, cord_classified, cord_reference)}
                entry["diagnosis"] = diagnose(config, reference, policies, gold, cord_classified, cord_reference)
        results[name] = entry

    # Self-consistency as a flag: does disagreement between samples point at the wrong fields?
    diffs = results["self_consistency"]["fields"]["diffs"]
    wrong = {(rid, f) for rid, d in diffs.items() for f in d if f != "missing"}
    flagged = {(rid, f) for rid, fs in flags.items() for f in fs}
    # `evidence` is wrong only when not verbatim (never, here), so its flags say only that samples
    # chose different fair quotes; the flags that matter are on the other fields.
    flagged_no_evidence = {(rid, f) for rid, f in flagged if f != "evidence"}
    results["self_consistency"]["flags"] = {
        "fields_flagged": len(flagged), "fields_wrong": len(wrong), "wrong_and_flagged": len(wrong & flagged),
        "without_evidence": {"fields_flagged": len(flagged_no_evidence),
                             "wrong_and_flagged": len(wrong & flagged_no_evidence),
                             "flagged_but_right": sorted(f"{rid}.{f}" for rid, f in flagged_no_evidence - wrong),
                             "wrong_but_not_flagged": sorted(f"{rid}.{f}" for rid, f in wrong - flagged_no_evidence)},
        "flagged_rules": sorted({rid for rid, _ in flagged}),
        "per_rule": {rid: fs for rid, fs in flags.items() if fs},
    }
    results["spend"] = {"usd": ledger_total(LEDGER_PATH), "calls": sum(1 for row in LEDGER_PATH.read_text().splitlines() if row.strip())
                        if LEDGER_PATH.exists() else 0}
    (RESULTS / "metrics.json").write_text(json.dumps(results, indent=1, default=str) + "\n", encoding="utf-8")
    (RESULTS / "summary.md").write_text(summary(results, len(interface)), encoding="utf-8")
    print(summary(results, len(interface)))
    return 0


SCOPE_GUARD = re.compile(r"(\S+) is about (?:the whole receipt but cited|items but cited none)")


def diagnose(config, reference, policies, gold, cord_classified, cord_reference) -> dict:
    """After the run, not in the plan: when the engine's EXC-04 guard stops on a rule's scope, copy
    that one rule's scope from rules.yaml and rerun, to see what the rest of the config decides.
    Reported as a diagnosis, never as the variant's result."""
    copied = []
    for _ in range(5):
        e2e = end_to_end(config, policies, gold, cord_classified, cord_reference)
        crash = next((v["crash"] for v in e2e.values() if "crash" in v), None)
        match = SCOPE_GUARD.search(crash or "")
        if not match:
            return {"scope_copied_from_rules_yaml": copied, "end_to_end": e2e}
        rule_id = match[1]
        copied.append(rule_id)
        config = config.model_copy(update={"rules": tuple(
            r.model_copy(update={"scope": reference.rule(rule_id).scope}) if r.id == rule_id else r for r in config.rules)})
    return {"scope_copied_from_rules_yaml": copied, "end_to_end": end_to_end(config, policies, gold, cord_classified, cord_reference)}


def _k(x: dict) -> str:
    return f"{x['k']} / {x['n']}"


def _mode(e2e: dict, mode: str) -> str:
    m = e2e.get(mode, {})
    return "crash" if "crash" in m else _k(m["exactly_right"] if "exactly_right" in m else m)


def summary(results: dict, n_rules: int) -> str:
    lines = ["# Experiment 3: results", "", "Generated by `make policy-config`. Read with PLAN.md.", "",
             "| Config | Rules exactly right | Config tests passed | Oracle mode | System mode | CORD decisions unchanged |",
             "|---|---|---|---|---|---|"]
    for name, r in results.items():
        if name in ("spend",):
            continue
        tests = r.get("config_tests", {})
        passed = f"{sum(v == 'pass' for v in tests.values())} / {len(tests)}" if tests else "-"
        e2e = r.get("end_to_end", {})
        rules = _k(r["fields"]["rules_exactly_right"]) if "fields" in r else f"{n_rules} / {n_rules} (the reference)"
        lines.append(f"| {name} | {rules} | {passed} | {_mode(e2e, 'oracle') if e2e else '-'} | "
                     f"{_mode(e2e, 'system') if e2e else '-'} | {_mode(e2e, 'cord_agreement') if e2e else '-'} |")
    lines += ["", "## Field accuracy", "", "| Field | " + " | ".join(n for n in results if "fields" in results[n]) + " |",
              "|---|" + "---|" * sum("fields" in r for r in results.values())]
    names = [n for n in results if "fields" in results[n]]
    for field in results[names[0]]["fields"]["fields"]:
        lines.append(f"| {field} | " + " | ".join(_k(results[n]["fields"]["fields"][field]) for n in names) + " |")
    flags = results["self_consistency"]["flags"]
    lines += ["", f"**Self-consistency flags:** {flags['fields_flagged']} fields flagged; {flags['wrong_and_flagged']} of the "
              f"{flags['fields_wrong']} wrong fields were among them.", "",
              f"**Spend:** ${results['spend']['usd']:.4f} over {results['spend']['calls']} calls."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
