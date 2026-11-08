"""The lookup report: what the semantic lookup decided, against a second opinion from the model and against labels.

`make lookup-report` (python -m evaluation.lookup_report). Offline: reads the committed traces and labels, writes
outputs/lookup_report.json.

The lookup decides lines (`mode: decide` in rules/lookup/config.yaml) or, with the kill switch `mode: shadow`, only
records what it would decide. Either way this report shows:
- every lookup answer against a person's label, where there is one; any restricted line it called safe is an alarm;
- every lookup answer against the model's answer for the same line (the model reads the whole receipt whenever any
  line goes to it, so its answer comes free); each disagreement is listed for a person;
- the audit sample: a fixed 2% of the lookup's decisions, marked in the trace, for a person to spot-check.

The checks, fixed before any client data:
1. No lookup answer calls a line safe that a person labelled restricted. If one does: switch to `mode: shadow`.
2. At least 99.5% of the lookup answers checked against a label agree with it. If not: switch to `mode: shadow`.
3. Every disagreement with the model and every audit line is looked at by a person (listed below).
4. τ_word was recalibrated on the client's own reviewed receipts (python -m validator.calibrate), not CORD's.

The labels here are the 32 receipts' answer key and the assistant's CORD labels, not a client's reviewers.
"""

import json
import sys

import yaml

from evaluation import cord
from evaluation.gold import load_gold
from validator.data import ROOT, read_jsonl
from validator.lookup import LOOKUP_DIR
from validator.taxonomy import load_taxonomy

TAXONOMY = load_taxonomy()
OUT = ROOT / "outputs" / "lookup_report.json"
MIN_AGREEMENT = 0.995


def _gold_categories() -> dict[str, str]:
    return {line_id: (labels["category"] if isinstance(labels, dict) else labels.category)
            for receipt in load_gold().values() for line_id, labels in receipt.items.items()}


def answers(traces: list[dict]) -> list[dict]:
    """Each line the lookup decided (or, in shadow mode, would have decided), with the model's answer where known."""
    out = []
    for t in traces:
        for x in t["classify"]["lines"]:
            if x.get("decided_by", "").startswith("lookup"):
                model = x.get("model_said", [None])[0]
                out.append({"line_id": x["line_id"], "lookup": x["category"], "model": model, "audit": bool(x.get("audit")), "why": x["why"]})
            elif x.get("shadow"):
                out.append({"line_id": x["line_id"], "lookup": x["shadow"], "model": x["category"], "audit": False, "why": x["why"]})
    return out


def report(traces: list[dict], labels: dict[str, str]) -> dict:
    lines = sum(len(t["classify"]["lines"]) for t in traces)
    a = answers(traces)
    checked = [x for x in a if x["line_id"] in labels]
    return {
        "lines": lines,
        "lookup_answers": len(a),
        "share": round(len(a) / max(1, lines), 4),
        "model_second_opinion": sum(x["model"] is not None for x in a),
        "agree_with_model": sum(x["model"] == x["lookup"] for x in a),
        "disagree_with_model": [{**x, "label": labels.get(x["line_id"])} for x in a if x["model"] is not None and x["model"] != x["lookup"]],
        "checked_against_a_label": len(checked),
        "agree_with_label": sum(x["lookup"] == labels[x["line_id"]] for x in checked),
        "restricted_called_safe": [{**x, "label": labels[x["line_id"]]} for x in checked
                                   if TAXONOMY[labels[x["line_id"]]] and not TAXONOMY[x["lookup"]]],
        "wrong_against_label": [{**x, "label": labels[x["line_id"]]} for x in checked if x["lookup"] != labels[x["line_id"]]],
        "audit_sample": [{**x, "label": labels.get(x["line_id"])} for x in a if x["audit"]],
    }


def checks(sets: dict[str, dict], config: dict) -> dict:
    checked = sum(s["checked_against_a_label"] for s in sets.values())
    agree = sum(s["agree_with_label"] for s in sets.values())
    record = LOOKUP_DIR / "calibration.json"
    calibration = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
    result = {
        "1 no lookup answer calls a restricted line safe": not any(s["restricted_called_safe"] for s in sets.values()),
        f"2 at least {MIN_AGREEMENT:.1%} of lookup answers agree with the label": checked > 0 and agree / checked >= MIN_AGREEMENT,
        "3 disagreements with the model and audit lines listed for a person": True,
        "4 τ_word recalibrated on the client's own reviewed receipts": bool(calibration.get("client_reviewed")),
    }
    safe = result["1 no lookup answer calls a restricted line safe"] and result[f"2 at least {MIN_AGREEMENT:.1%} of lookup answers agree with the label"]
    return {"mode": config.get("mode", "decide"), "checks": result,
            "action": "keep deciding" if safe else "switch to mode: shadow",
            "note": (f"{checked} lookup answers checked against a label, {agree} agreeing. The labels are the 32 receipts' answer key and the "
                     "assistant's CORD labels, not a client's reviewers; τ_word is still CORD's until recalibrated on a client's reviews.")}


def main() -> int:
    config = yaml.safe_load((LOOKUP_DIR / "config.yaml").read_text(encoding="utf-8"))
    cord_labels = {k: v["category"] for k, v in cord.load_labels().items()}
    sets = {"32 receipts": report(list(read_jsonl(ROOT / "outputs" / "traces.jsonl")), _gold_categories()),
            "CORD validation": report(list(read_jsonl(ROOT / "outputs" / "cord" / "traces.jsonl")), cord_labels)}
    out = {"sets": sets, **checks(sets, config)}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, s in sets.items():
        print(f"{name}: the lookup answered {s['lookup_answers']} of {s['lines']} lines ({s['share']:.0%}); agrees with the label on "
              f"{s['agree_with_label']} of {s['checked_against_a_label']}; with the model on {s['agree_with_model']} of {s['model_second_opinion']}; "
              f"restricted called safe: {len(s['restricted_called_safe'])}; audit sample: {len(s['audit_sample'])}")
        for d in s["disagree_with_model"]:
            print(f"   disagrees with the model: {d['line_id']} lookup {d['lookup']}, model {d['model']}, label {d['label']}")
        for d in s["audit_sample"]:
            print(f"   audit: {d['line_id']} lookup {d['lookup']}, label {d['label']}")
    print(f"mode {out['mode']}; action: {out['action']}")
    for k, ok in out["checks"].items():
        print(f"   {'✓' if ok else '✗'} {k}")
    print(out["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
