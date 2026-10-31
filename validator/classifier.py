"""Item classification: one model call per receipt, validated in code.

The model returns, for each line, a category from the 21 in the taxonomy, an item kind for the three
things the policies name that have no category, the recipient the line names (if any), and a short
reason. Code then sets `restricted` from the taxonomy, so the two can never disagree, and takes the
confidence from the logprobs of the category's tokens (DECISIONS.md, section 3). A non-itemized line
is `other` by definition: set in code, with no call and full confidence (EXC-03).

The prompt's examples are written for this project. None is taken from the receipts, and the
taxonomy's own examples, which do appear in the receipts, are left out on purpose so that the score
on those items is a real test.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from validator.data import ReceiptInput
from validator.llm import LLM, Completion, LLMError, complete_json
from validator.models import ClassifiedItem, ClassifiedReceipt
from validator.normaliser import normalise
from validator.taxonomy import load_taxonomy

PROMPT_VERSION = "classify-v1"
TAXONOMY = load_taxonomy()
# Settings the M7 smoke test established: logprobs carry information only at temperature 1.
CALL = {"logprobs": True, "top_logprobs": 5, "temperature": 1.0, "max_tokens": 3000}

DEFINITIONS = {
    "alcohol": "drinks that contain alcohol: beer, wine, spirits, sake, cocktails, corkage; also wine or spirits sold as a product for cooking",
    "tobacco": "cigarettes, cigars, cigarillos, loose tobacco, vapes and e-liquids",
    "personal_electronics": "consumer devices (headphones, earbuds, tablets, e-readers, smart watches, fitness trackers, cameras, drones, speakers, games consoles, televisions, item trackers) and accessories made for them, such as cases, straps and chargers",
    "gift": "things made to be given: flowers, gift baskets, greeting cards, branded merchandise",
    "gift_card": "gift cards, prepaid cards, vouchers and any other cash equivalent",
    "household_goods": "cleaning and laundry products, paper towels, tissues, toilet paper, rubbish bags, kitchenware, drinkware and barware, bedding, furniture, small appliances, home décor",
    "personal_care": "toiletries, cosmetics, razors, toothbrushes, haircuts, salon, spa and massage",
    "clothing": "apparel, footwear and fashion accessories such as belts, ties, scarves and bags",
    "gambling": "casino chips, bets, lottery tickets",
    "jewelry": "non-electronic watches, bracelets, rings, necklaces, cufflinks, precious-metal items",
    "food_beverage": "meals, dishes, snacks, desserts, catering and non-alcoholic drinks, including dishes cooked with alcohol and alcohol-free versions of drinks",
    "computer_accessory": "cables, adapters, docking stations, mice, keyboards, monitors, laptop stands, webcams",
    "office_supplies": "stationery, paper, pens, notebooks, printing, postage, shipping",
    "lodging": "room charges, resort fees, lodging taxes",
    "ground_transport": "taxi, rideshare, transit, shuttles, parking, tolls, fuel",
    "airfare": "flight tickets, seat fees, bag fees",
    "laundry_service": "laundry, dry cleaning, pressing",
    "health_otc": "over-the-counter medicine, lozenges, first aid",
    "software_subscription": "software and SaaS subscriptions and licences",
    "service_fee": "service charges, gratuities, tips, delivery fees, booking fees",
    "other": "anything that fits none of the above, including a charge whose contents are unknown and the three item kinds below",
}
SYSTEM = f"""You classify the line items on an expense receipt.

For each line, decide what the item IS, not what words its name contains, and pick exactly one category:
{chr(10).join(f"- {name}: {text}" for name, text in DEFINITIONS.items())}

Rules:
- A name can mislead. A dish cooked with a spirit is food_beverage; an alcohol-free beer is food_beverage; a set of martini glasses is household_goods.
- What an item is and why it was bought are separate. A bottle of sake bought as a present is alcohol, and a silver ring bought for a client is jewelry.
- The merchant, its category code and the declared expense type are context only. Merchants sell many things and employees mis-declare expense types, so never let them override the item itself.
- For lines not in English an English gloss is provided. The original text is authoritative; use the gloss to read it.

item_kind: set it only for these three, and give them category "other":
- "entertainment_venue": tickets or fees for golf, sporting events, theatre, concerts or similar venues (for example, football match tickets)
- "mobile_phone": a mobile phone handset
- "prescription_medication": medicine dispensed on prescription
Otherwise item_kind is null.

recipient_name and recipient_type: fill them only when the line itself says who the item is for or who the guest is. recipient_type is "client" (a client, prospect or anyone at another company), "employee" (a colleague or anyone at our own company) or "government_official" (a public official, public employee, government body or state-owned enterprise employee). If the line names no one, both are null.

reason: one short sentence on what the item is. For "other", say what the item is.

Reply in json only, one entry per input line, in the input order, with the keys in this order:
{{"items": [{{"line_id": "...", "category": "...", "item_kind": null, "recipient_name": null, "recipient_type": null, "reason": "..."}}]}}"""


class LineReply(BaseModel):
    line_id: str
    category: str
    item_kind: Literal["entertainment_venue", "mobile_phone", "prescription_medication"] | None = None
    recipient_name: str | None = None
    recipient_type: Literal["client", "employee", "government_official"] | None = None
    reason: str


class Reply(BaseModel):
    items: list[LineReply]


@dataclass(frozen=True)
class Classification:
    receipt: ClassifiedReceipt
    prompt_version: str
    cache_key: str | None  # None when no call was made (non-itemized)
    model: str | None


def _messages(receipt: ReceiptInput, glosses: dict[str, str], context: bool) -> list[dict]:
    payload = {}
    if context:
        payload["receipt"] = {
            "merchant": receipt.merchant_name,
            "merchant_category": receipt.mcc_description,
            "declared_expense_type": receipt.submitted_expense_type,
        }
    payload["items"] = [
        {"line_id": line.line_id, "text": normalise(line.description_raw), **({"english": glosses[line.line_id]} if line.line_id in glosses else {})}
        for line in receipt.line_items
    ]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def _problems(receipt: ReceiptInput):
    wanted = [line.line_id for line in receipt.line_items]

    def check(reply: Reply) -> list[str]:
        returned = [item.line_id for item in reply.items]
        problems = [f"line {line_id} is missing" for line_id in wanted if line_id not in returned]
        problems += [f"line {line_id} appears {returned.count(line_id)} times" for line_id in set(returned) if returned.count(line_id) > 1]
        problems += [f"line {line_id} is not on this receipt" for line_id in set(returned) - set(wanted)]
        for item in reply.items:
            if item.category not in TAXONOMY:
                problems.append(f"{item.line_id}: {item.category!r} is not one of the categories")
            if item.item_kind and item.category != "other":
                problems.append(f"{item.line_id}: an item_kind needs category \"other\"")
            if item.recipient_type and not item.recipient_name:
                problems.append(f"{item.line_id}: recipient_type is set but recipient_name is not")
        return problems

    return check


def category_confidences(completion: Completion, reply: Reply) -> list[float]:
    """Probability of each line's category tokens, in reply order. If the category values cannot be
    lined up with the reply, every line gets 0, so each raises EXC-03 rather than being trusted."""
    spans = [(m[1], m.start(1), m.end(1)) for m in re.finditer(r'"category"\s*:\s*"([^"]*)"', completion.content)]
    if [value for value, _, _ in spans] != [item.category for item in reply.items]:
        return [0.0] * len(reply.items)
    try:
        return [completion.span_probability(start, end) for _, start, end in spans]
    except LLMError:  # the tokens themselves do not match the text
        return [0.0] * len(reply.items)


def classify(receipt: ReceiptInput, glosses: dict[str, str], llm: LLM, *, context: bool = True, run: str = "") -> Classification:
    """`context=False` leaves out the merchant and expense type (the merchant ablation).
    `run` names a repeat of the same request, so it is a fresh call rather than a cache hit."""
    prompt_version = PROMPT_VERSION + ("" if context else "-nocontext") + (f"-{run}" if run else "")
    if not receipt.itemized:
        items = {
            line.line_id: ClassifiedItem(line_id=line.line_id, category="other", restricted=TAXONOMY["other"], confidence=1.0,
                                         reason="Not itemized: the receipt shows only a total, so its contents are unknown.")
            for line in receipt.line_items
        }
        return Classification(ClassifiedReceipt(receipt=receipt, items=items), prompt_version, None, None)

    reply, completion = complete_json(llm, _messages(receipt, glosses, context), Reply, purpose="classify",
                                      prompt_version=prompt_version, check=_problems(receipt), **CALL)
    confidences = category_confidences(completion, reply)
    items = {
        line.line_id: ClassifiedItem(
            line_id=line.line_id,
            category=line.category,
            restricted=TAXONOMY[line.category],
            confidence=round(confidence, 6),
            recipient_name=line.recipient_name,
            recipient_type=line.recipient_type,
            item_kind=line.item_kind,
            reason=line.reason,
        )
        for line, confidence in zip(reply.items, confidences)
    }
    return Classification(ClassifiedReceipt(receipt=receipt, items=items), prompt_version, completion.cache_key[:24], completion.model)
