"""The model stage: the submitted classifier's prompt, model and validation, at a chosen temperature.

It differs from `validator.classifier.classify` only in the temperature. Calls go to this experiment's own
cache, so nothing from the committed run is reused.
"""

from validator import classifier as v1
from validator.classifier import Classification
from validator.data import ReceiptInput
from validator.llm import LLM, complete_json
from validator.models import ClassifiedItem, ClassifiedReceipt


def classify_at(receipt: ReceiptInput, glosses: dict[str, str], llm: LLM, *, temperature: float,
                context: bool = True, run: str = "") -> Classification:
    if not receipt.itemized:
        return v1.classify(receipt, glosses, llm, context=context, run=run)  # no call: `other` in code
    call = {**v1.CALL, "temperature": temperature}
    prompt_version = v1.PROMPT_VERSION + ("" if context else "-nocontext") + (f"-{run}" if run else "")
    reply, completion = complete_json(llm, v1._messages(receipt, glosses, context), v1.Reply, purpose="classify",
                                      prompt_version=prompt_version, check=v1._problems(receipt), **call)
    confidences, fallback = v1.category_confidences(completion, reply)
    starts = [start for _, start, _ in v1._category_spans(completion)] if fallback is None else []
    alternatives = {line.line_id: v1.category_alternatives(completion, start, line.category) for line, start in zip(reply.items, starts)}
    items = {line.line_id: ClassifiedItem(
        line_id=line.line_id, category=line.category, restricted=v1.TAXONOMY[line.category], confidence=round(confidence, 6),
        recipient_name=line.recipient_name, recipient_type=line.recipient_type, item_kind=line.item_kind, reason=line.reason)
        for line, confidence in zip(reply.items, confidences)}
    return Classification(ClassifiedReceipt(receipt=receipt, items=items), prompt_version, completion.cache_key[:24], completion.model,
                          cached=completion.cached, retried_after=completion.retried_after,
                          confidence_fallback=fallback, alternatives=alternatives)
