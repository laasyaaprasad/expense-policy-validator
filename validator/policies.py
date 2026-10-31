"""Splits the policy documents into rule records, one per definition such as `**ALC-01 — Title.**`."""

import re
from dataclasses import dataclass
from pathlib import Path

from validator.data import POLICY_DIR

RULE_ID = re.compile(r"\b[A-Z]+-\d{2}\b")
_DEFINITION = re.compile(r"^\*\*([A-Z]+-\d{2}) — (.+?)\.?\*\*")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)")


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    doc: str  # file name, e.g. meals-and-entertainment-policy.md
    section: str | None  # nearest `##` heading above the definition
    text: str  # the definition and everything after it, up to the next rule or heading
    mentions: frozenset[str]  # other rule IDs the text refers to


def parse_policy(path: Path) -> list[Rule]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rules: list[Rule] = []
    section: str | None = None
    open_rule: tuple[str, str, int, str | None] | None = None  # id, title, first line, section

    def close(end: int) -> None:
        if open_rule is None:
            return
        rule_id, title, start, rule_section = open_rule
        text = "\n".join(lines[start:end]).strip()
        rules.append(Rule(
            id=rule_id,
            title=title,
            doc=path.name,
            section=rule_section,
            text=text,
            mentions=frozenset(RULE_ID.findall(text)) - {rule_id},
        ))

    for index, line in enumerate(lines):
        if heading := _HEADING.match(line):
            close(index)
            open_rule = None
            section = heading[2].strip() if len(heading[1]) >= 2 else None
        elif definition := _DEFINITION.match(line):
            close(index)
            open_rule = (definition[1], definition[2].strip(), index, section)
    close(len(lines))
    return rules


def parse_all(policy_dir: Path = POLICY_DIR) -> list[Rule]:
    """Every rule definition in every document, in file order. May contain duplicates."""
    return [rule for path in sorted(policy_dir.glob("*.md")) for rule in parse_policy(path)]


def load_policies(policy_dir: Path = POLICY_DIR) -> dict[str, Rule]:
    """Rule ID → rule. Raises if an ID is defined twice."""
    rules: dict[str, Rule] = {}
    for rule in parse_all(policy_dir):
        if rule.id in rules:
            raise ValueError(f"{rule.id} is defined in both {rules[rule.id].doc} and {rule.doc}")
        rules[rule.id] = rule
    return rules
