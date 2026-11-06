# Experiment 2: a decision model as a yes/no verifier on restricted lines (plan)

Written before any call on the data. The submitted system is not changed by anything here.

## Change before the first run: Mammoth-1 instead of Jev (2026-11-06)

The plan below was written for Jev. Jev could not be reached without paying: OpenRouter has no real Jev, and Vercel AI Gateway refuses every Jev version on a free or trial account (details under "What the test calls found"). So, before any call on the data, the verifier model became **Baseten's Mammoth-1** (`baseten/mammoth-1`), a 27B decision model fine-tuned from Qwen3.5-27B, which Vercel lists as first on the Jev Decision Index. It is reached through Vercel AI Gateway's Decision API (`POST https://ai-gateway.vercel.sh/v1/decisions`), with a `predicate` question that returns the probability that the answer is yes, as Jev's Noul does.

What changes, and nothing else:
- **The question format:** a predicate takes only `instructions`, so the same yes/no criteria are written into the instruction text instead of a separate `criteria` field.
- **The input:** the same fields as before, as plain text.
- **Who sees line text:** Vercel and Baseten, instead of Vercel and TypeSafe.
- **Cost:** $0.10 per million input tokens (about $0.0000115 a question), so about $0.0006 for all 49 lines.

The lines checked, the two variants, the 0.5 and 0.1 thresholds, the measures and what counts as success stay exactly as written below. Where the text says Jev, read Mammoth-1.

## Question

The current system's real-receipt false flags come from lines it confidently calls restricted when they aren't: "*Rhum" flavouring as alcohol at 0.95, a paper bag as household goods at 0.92 (`docs/evaluation.md`, section 8). Can a second, cheap model that answers one narrow yes/no question about each restricted line catch those, without clearing real violations?

## Why Jev, and why this stage

[Jev](https://docs.typesafe.ai/introduction.md) (TypeSafe) answers typed questions instead of writing text. A [Noul](https://docs.typesafe.ai/primitives/noul.md) question returns one number: the probability that the answer is yes. TypeSafe trains it for calibrated probabilities, and two of its own patterns match this use: [screening with a Noul](https://docs.typesafe.ai/patterns/noul-screen.md) and [checking each field with Jev in a cascade](https://docs.typesafe.ai/cookbooks/sde_cascade.md). Published tests found it weak with many categories and non-English text, so it isn't asked to classify (that was option 1, a later experiment), only to confirm one category at a time. It isn't used for the disposition, which the brief keeps in code.

## What the test calls found (2026-11-06)

- **OpenRouter has no Jev with probabilities.** Its only TypeSafe listing, `typesafe/jev-router`, is a router: a test call was answered by Google's Gemini 3.8 Flash, returned no logprobs or probabilities, and spent its 17 tokens reasoning without an answer ($0.0000833). `typesafe/jev-latest` is not a valid OpenRouter model.
- **So the experiment uses Jev's own request format** (TypeSafe's System One API), reached through **Vercel AI Gateway's TypeSafe-compatible endpoint**, `POST https://ai-gateway.vercel.sh/typesafe/v1/systemone`, with an AI Gateway key (`AI_GATEWAY_API_KEY`). It is pinned to `typesafe-ai/jev-1.14.0`, not the moving `typesafe-ai/jev`, so a rerun hits the same model. Price: $0.042 per million input tokens; output is free. TypeSafe's direct API (`api.typesafe.ai`) needs a TypeSafe key instead, and rejected the gateway key (401).
- **The gateway needs paid credits for Jev:** its first call was refused with "Free tier users do not have access to this model" (403), at no charge.

## Design

- **Which lines:** every line the submitted classifier labels restricted, with its committed answer (`outputs/classifications.jsonl`, `outputs/cord/classifications.jsonl`, `outputs/dev/configured.jsonl`). That is 21 lines on the 32 receipts, 10 on CORD and 18 in the dev set.
- **State sent to Jev:** the line text, its English gloss if any, the merchant, its category description and the declared expense type. That's exactly what the classifier already sees: no amounts, employee fields or labels.
- **The question:** one Noul per line, "Is the item itself <category>?", with `true` and `false` criteria taken word for word from the classifier's own definitions (`validator/classifier.py`, `DEFINITIONS` and its rules about misleading names). For alcohol, for example: yes for a drink that contains alcohol, or wine or spirits sold as a product; no for a dish cooked with alcohol or an alcohol-free drink. Nothing is written from CORD's labelling guide, so the verifier isn't fitted to CORD's labels.
- **Two variants, both fixed now and not tuned:**
  1. **Doubt → review.** The line's confidence becomes the lower of DeepSeek's confidence and Jev's yes-probability. The engine's existing 0.5 threshold then sends a doubted line to review (EXC-03) instead of flagging it. This never changes a category, so it can turn a flag into a review but never pass a violation outright.
  2. **Veto → runner-up.** If Jev's yes-probability is below 0.1, the line takes DeepSeek's most likely unrestricted alternative from the committed trace, and the receipt can pass. This is stronger, and riskier.
- **Caching and spend:** every Jev response is cached by request, so reruns are offline, and every call is logged to this experiment's `spend.jsonl`.

## Measures

- **Lines:** for the checked lines, how well Jev's yes-probability separates truly restricted lines from false ones (counts at 0.5 and 0.1, and AUROC where there are both kinds).
- **28 scored receipts:** exactly right, false positives, and above all **missed violations**. A verifier that clears real alcohol fails, however many false flags it removes.
- **CORD:** false flags, false reviews, flag precision and missed flags, against the labels' own decisions.
- **Cost and time** per checked line.

## What would count as it working

On CORD, fewer false flags than the current 6 / 98. On the 28 receipts, still 28 / 28 exactly right with no missed violation. Variant 1 counts as a win only if the flags it removes become reviews without adding missed violations; variant 2 only if it removes false alarms outright with none of those.

## Limits, stated in advance

- **Small numbers:** 10 checked lines on CORD (one truly restricted) and 21 on the receipts, so this shows a direction, not a rate.
- **Taxonomy vs labels:** CORD's labels call packaging `other`, while the taxonomy counts drinkware as household goods. A verifier that follows the taxonomy may rightly say yes to "CUP 14 OZ", so some false flags are disagreements between the taxonomy and the labelling guide, not model errors. They are reported separately.
- **One check, one direction:** only restricted lines are checked, so the verifier can't catch a restricted item the classifier missed (there are none on the 28 receipts).

## Before it can run

- **A Vercel AI Gateway key** in `.env` as `AI_GATEWAY_API_KEY`. Jev needs paid credits on it; Mammoth-1 runs on the free tier. Vercel and the model's host become two more services that see line text, so the README must name them.
- **Expected spend:** about 49 lines at roughly 200 input tokens each, under $0.001.
