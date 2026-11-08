"""The shadow report: what the semantic lookup would have decided, against what the model and a person decided.

`make shadow` (python -m evaluation.shadow). Offline: reads the committed traces and labels, writes outputs/shadow.json.

While rules/lookup/config.yaml says `mode: shadow`, the lookup decides nothing: it records the category it would
have given (the trace's `shadow`), and the model decides every line. This report is how the switch to `mode: decide`
is earned. The criteria are fixed here, before any client data is seen:

1. Volume: at least 500 of the lookup's would-be decisions checked against a person's label on the client's own
   receipts.
2. Safety: none of them calls a line safe that a person labelled restricted.
3. Accuracy: at least 99.5% of them agree with the person's label.
4. Every disagreement between the lookup and the model has been looked at by a person (the report lists them).
5. τ_word was recalibrated on the client's reviewed receipts (python -m validator.calibrate), not CORD's.

The labels here are the answer key of the 32 receipts and the assistant's CORD labels: neither is a client's
reviewers, so criteria 1 and 5 can't be met with this data, whatever the numbers say.
"""

import json
import sys
from pathlib import Path

from evaluation import cord
from evaluation.gold import load_gold
from validator.data import ROOT, read_jsonl
from validator.lookup import LOOKUP_DIR
from validator.taxonomy import load_taxonomy

import yaml

TAXONOMY = load_taxonomy()
OUT = ROOT / "outputs" / "shadow.json"
MIN_REVIEWED, MIN_AGREEMENT = 500, 0.995


def _gold_categories() -> dict[str, str]:
    out = {}
    for receipt in load_gold().values():
        for line_id, labels in receipt.items.items():
            out[line_id] = labels["category"] if isinstance(labels, dict) else labels.category
    return out


def report(traces: list[dict], labels: dict[str, str]) -> dict:
    lines = [x for t in traces for x in t["classify"]["lines"]]
    shadow = [x for x in lines if x.get("shadow")]
    reviewed = [x for x in shadow if x["line_id"] in labels]
    agree_label = [x for x in reviewed if x["shadow"] == labels[x["line_id"]]]
    return {
        "lines": len(lines),
        "would_decide": len(shadow),
        "share": round(len(shadow) / max(1, len(lines)), 4),
        "agree_with_model": sum(x["shadow"] == x["category"] for x in shadow),
        "disagree_with_model": [{"line_id": x["line_id"], "lookup": x["shadow"], "model": x["category"], "label": labels.get(x["line_id"]),
                                 "why": x["why"]} for x in shadow if x["shadow"] != x["category"]],
        "checked_against_a_label": len(reviewed),
        "agree_with_label": len(agree_label),
        "restricted_called_safe": [{"line_id": x["line_id"], "lookup": x["shadow"], "label": labels[x["line_id"]]}
                                   for x in reviewed if TAXONOMY[labels[x["line_id"]]] and not TAXONOMY[x["shadow"]]],
        "wrong_against_label": [{"line_id": x["line_id"], "lookup": x["shadow"], "label": labels[x["line_id"]], "why": x["why"]}
                                for x in reviewed if x["shadow"] != labels[x["line_id"]]],
    }


def verdict(sets: dict[str, dict], config: dict) -> dict:
    reviewed = sum(s["checked_against_a_label"] for s in sets.values())
    agree = sum(s["agree_with_label"] for s in sets.values())
    record = LOOKUP_DIR / "calibration.json"
    calibration = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
    criteria = {
        f"1 at least {MIN_REVIEWED} would-be decisions checked against a client reviewer's label": False,  # no client data here
        "2 none calls a restricted line safe": not any(s["restricted_called_safe"] for s in sets.values()),
        f"3 at least {MIN_AGREEMENT:.1%} agree with the label": reviewed > 0 and agree / reviewed >= MIN_AGREEMENT,
        "4 no disagreement with the model left unreviewed (none is recorded as reviewed here)": not any(s["disagree_with_model"] for s in sets.values()),
        "5 τ_word recalibrated on the client's reviewed receipts": bool(calibration.get("client_reviewed")),
    }
    return {"mode": config.get("mode", "decide"), "criteria": criteria,
            "ready_to_decide": all(criteria.values()),
            "note": ("The labels here are the 32 receipts' answer key and the assistant's CORD labels, not a client's reviewers: "
                     f"{reviewed} would-be decisions checked, {agree} agreeing. Stay in shadow mode until a client's reviewed receipts meet "
                     "every criterion.")}


def main() -> int:
    config = yaml.safe_load((LOOKUP_DIR / "config.yaml").read_text(encoding="utf-8"))
    cord_labels = {k: v["category"] for k, v in cord.load_labels().items()}
    sets = {"32 receipts": report(list(read_jsonl(ROOT / "outputs" / "traces.jsonl")), _gold_categories()),
            "CORD validation": report(list(read_jsonl(ROOT / "outputs" / "cord" / "traces.jsonl")), cord_labels)}
    out = {"sets": sets, **verdict(sets, config)}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, s in sets.items():
        print(f"{name}: the lookup would decide {s['would_decide']} of {s['lines']} lines ({s['share']:.0%}); agrees with the model on "
              f"{s['agree_with_model']}; with the label on {s['agree_with_label']} of {s['checked_against_a_label']}; restricted called safe: "
              f"{len(s['restricted_called_safe'])}")
        for d in s["disagree_with_model"]:
            print(f"   disagrees with the model: {d}")
    print(f"mode {out['mode']}; ready to decide: {out['ready_to_decide']}")
    for k, ok in out["criteria"].items():
        print(f"   {'✓' if ok else '✗'} {k}")
    print(out["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
