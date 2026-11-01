"""rules.yaml encodes every in-scope rule, and every number, severity, table and quote in it
can be found in the policy text. These tests check the YAML against the documents, not against itself."""

import re

import pytest

from evaluation.gold import in_scope, load_gold
from validator.data import DATA_DIR, POLICY_DIR, load_receipts
from validator.policies import RULE_ID, load_policies
from validator.rules_config import load_rules, policy_version
from validator.taxonomy import load_taxonomy

NUMBER_WORDS = {2: "two", 4: "four"}
REFERENCE_KINDS = {"pre_approval", "vp_approval", "manager_approval", "baggage_delay"}


def plain(text: str) -> str:
    """Markdown emphasis and code marks removed, whitespace collapsed."""
    return " ".join(re.sub(r"[*`]", "", text).split())


@pytest.fixture(scope="module")
def config():
    return load_rules()


@pytest.fixture(scope="module")
def policies():
    return load_policies()


@pytest.fixture(scope="module")
def gold():
    return load_gold()


def scored_gold_rule_ids(gold):
    return {
        finding.rule_id
        for g in gold.values() if in_scope(g.receipt_id)
        for finding in (*g.exceptions, *g.review_signals)
    }


def test_every_rule_is_encoded_or_out_of_scope(config, policies):
    encoded = {rule.id for rule in config.rules}
    assert set(config.out_of_scope) == {"TIME-04"}  # trip records hold no start or end dates
    assert encoded | set(config.out_of_scope) == set(policies)


def test_each_rule_names_its_document(config, policies):
    for rule in config.rules:
        assert rule.doc == policies[rule.id].doc, rule.id


def test_evidence_is_verbatim_policy_text(config, policies):
    paraphrased = [rule.id for rule in config.rules if plain(rule.evidence) not in plain(policies[rule.id].text)]
    assert paraphrased == []


def test_numeric_params_appear_in_rule_text(config, policies):
    # Catches a threshold invented or mistyped while drafting the YAML.
    missing = []
    for rule in config.rules:
        text = plain(policies[rule.id].text)
        for name, value in rule.params.items():
            number = int(value) if value == value.to_integral_value() else value
            candidates = {str(number), NUMBER_WORDS.get(number, str(number)), f"{number:02d}:00" if isinstance(number, int) else str(number)}
            if not any(re.search(rf"\b{re.escape(c)}\b", text) for c in candidates):
                missing.append((rule.id, name, value))
    assert missing == []


def test_severity_is_stated_in_rule_text(config, policies):
    for rule in config.rules:
        if rule.kind != "exception":
            continue
        severities = rule.severity.values() if isinstance(rule.severity, dict) else [rule.severity]
        for severity in severities:
            assert f"**{severity}**" in policies[rule.id].text, (rule.id, severity)


def test_only_health_01_has_two_severities(config):
    assert {rule.id for rule in config.rules if isinstance(rule.severity, dict)} == {"HEALTH-01"}


def test_severity_matches_gold(config, gold):
    for g in gold.values():
        for exception in g.exceptions:
            if exception.rule_id in config.out_of_scope:
                continue
            assert config.rule(exception.rule_id).severity == exception.severity, (g.receipt_id, exception.rule_id)


def test_whole_receipt_scope_matches_exc_04(config, policies):
    sentence = re.search(r"Rules about the receipt as a whole — (.+?) — cite no line items", plain(policies["EXC-04"].text))
    assert sentence, "EXC-04 wording changed"
    expected = set(RULE_ID.findall(sentence[1])) - set(config.out_of_scope)
    assert {rule.id for rule in config.rules if rule.scope == "receipt"} == expected


def test_tiers_match_the_scored_answers(config, gold):
    tier_1 = {rule.id for rule in config.rules if rule.tier == 1}
    tier_2 = {rule.id for rule in config.rules if rule.tier == 2}
    assert tier_1 == scored_gold_rule_ids(gold)
    assert len(tier_1) == 20 and len(tier_2) == 14


def test_review_signal_rules(config):
    assert {rule.id for rule in config.rules if rule.kind == "review_signal"} == {"MCC-02", "TIME-01", "TIME-02"}


def test_overrides_point_at_encoded_rules(config):
    encoded = {rule.id for rule in config.rules}
    for rule in config.rules:
        assert set(rule.overrides) <= encoded, rule.id
        assert set(rule.line_overrides) <= encoded, rule.id
        if rule.overrides:
            assert rule.applies_to.regions, f"{rule.id} overrides globally; only regional rules should"


def test_mcc_risk_table_matches_policy(config):
    text = (POLICY_DIR / "merchant-category-risk.md").read_text()
    parsed: dict[str, set[str]] = {}
    for mcc, tier in re.findall(r"^\| (\d{4}) \|[^|]+\| \**(\w+)\** \|", text, flags=re.MULTILINE):
        parsed.setdefault(tier.lower(), set()).add(mcc)
    assert {tier: set(codes) for tier, codes in config.mcc_risk.items()} == parsed


def test_expense_type_table_matches_doc_03(config, policies):
    parsed = {}
    for types, categories in re.findall(r"^\| ([A-Z][^|`]+?) \| (`.+?`) \|", policies["DOC-03"].text, flags=re.MULTILINE):
        for expense_type in types.split(", "):
            parsed[expense_type] = set(re.findall(r"`(\w+)`", categories))
    assert {k: set(v) for k, v in config.expense_type_covers.items()} == parsed


def test_regions_match_data_dictionary(config):
    text = (DATA_DIR / "README.md").read_text()
    for region in ("APAC", "EMEA"):
        listed = re.search(rf"`{region}` \(([A-Z, ]+)\)", text)
        assert listed, region
        assert set(config.regions[region]) == set(listed[1].split(", ")), region


def test_on_premises_mccs_match_alcohol_scope(config, policies):
    note = re.search(r"\(MCC ([\d, and]+)\)", plain(policies["ALC-02"].text))
    assert note, "ALC-01/02 scope note changed"
    assert set(config.on_premises_mccs) == set(re.findall(r"\d{4}", note[1]))


def test_meal_expense_types_match_meal_01(config, policies):
    text = plain(policies["MEAL-01"].text)
    assert all(expense_type in text for expense_type in config.meal_expense_types)
    assert set(config.meal_expense_types) <= set(config.expense_type_covers)


def test_applies_to_uses_known_values(config):
    taxonomy = load_taxonomy()
    for rule in config.rules:
        applies = rule.applies_to
        assert set(applies.categories) <= set(taxonomy), rule.id
        assert set(applies.mcc_risk) <= set(config.mcc_risk), rule.id
        assert set(applies.expense_types) <= set(config.expense_type_covers), rule.id
        assert set(applies.regions) <= set(config.regions), rule.id
        assert rule.requires_reference in REFERENCE_KINDS | {None}, rule.id
    assert set(config.reference_prefixes.values()) <= REFERENCE_KINDS


def test_home_currency_covers_every_country(config):
    countries = {"US"} | {c for members in config.regions.values() for c in members}
    assert countries <= set(config.home_currency)
    assert {r.employee_country for r in load_receipts()} <= set(config.home_currency)


def test_policy_version_is_a_short_hash():
    assert re.fullmatch(r"[0-9a-f]{12}", policy_version())
