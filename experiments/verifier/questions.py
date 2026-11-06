"""The yes/no question the verifier answers for a line the classifier calls restricted.

The yes and no descriptions come from the classifier's own definitions and rules
(validator/classifier.py), so the verifier judges the same taxonomy the classifier was given, and
nothing is taken from CORD's labelling guide. A predicate takes only instructions, so both
descriptions are written into them (PLAN.md, the change before the first run).
"""

from validator.classifier import DEFINITIONS
from validator.data import ReceiptInput
from validator.normaliser import normalise

# The classifier prompt's rules about misleading names.
NOT_THE_NAME = ("Decide what the item IS, not what words its name contains. The merchant and the declared "
                "expense type are context only and never override the item itself.")
# Where the taxonomy itself names the near miss: dishes cooked with alcohol and alcohol-free drinks.
INSTEAD = {"alcohol": f"It is food_beverage instead: {DEFINITIONS['food_beverage']}."}
QUESTION_ID = "is_category"


def predicate(category: str) -> dict:
    label = category.replace("_", " ")
    yes = f"The item is {label}: {DEFINITIONS[category]}."
    no = INSTEAD.get(category, f"The item is not {label}.")
    return {"type": "predicate", "name": QUESTION_ID,
            "instructions": f"Is the item on this receipt line itself {label}? Yes: {yes} No: {no} {NOT_THE_NAME}"}


def input_text(receipt: ReceiptInput, line_text: str, english: str | None) -> str:
    """What the classifier sees for the line, and nothing more: no amounts, employee fields or labels."""
    rows = [f"Receipt line: {normalise(line_text)}"]
    if english:
        rows.append(f"English gloss: {english}")
    rows += [f"Merchant: {receipt.merchant_name}", f"Merchant category: {receipt.mcc_description}",
             f"Declared expense type: {receipt.submitted_expense_type}"]
    return "\n".join(rows)
