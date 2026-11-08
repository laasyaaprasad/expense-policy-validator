# Design decisions

The research done before building, on 2026-10-30. For each decision the brief leaves open: the options considered, the one picked, and the finding behind it. Where the build departed from the research, an **As built** line says so. Sources are at the end.

| Decision | Picked | Main reason |
|---|---|---|
| Where AI sits | AI reads the receipt; code applies the policy | Every expense tool looked at draws this line |
| Rule encoding | `rules.yaml` for data, a small Python check per rule | A hand-written engine fits ~30 rules; chained rules are the known trap |
| Classifier | One structured call per receipt, validated in code | Batching items barely costs accuracy |
| Confidence | Logprobs of the category tokens | Self-reported LLM confidence is near-constant |
| Translation | A separate English gloss, shown beside the original | Direct inference beats pre-translation for major languages |
| Retrieval | A structured index over each rule's `applies_to` | A missed rule is a missed violation, so it can't be fuzzy |
| Evidence | The policy text stored word for word, explanations from templates | A stored quote can't be invented |
| Model | DeepSeek V4.1 Flash | Strong with reasoning off, logprobs, low cost |
| Evaluation | A plain script with Wilson intervals | Exact labels need no LLM-eval framework |
| Semantic lookup | A local lookup decides lines a person approved, every word matched by meaning; the model the rest | Repeats are common; looser matching let look-alike traps through |

## 1. Where AI sits

- **Options:** an LLM that reads the policies and judges each receipt; or AI that only reads the receipt, with the policy applied by deterministic code.
- **Picked:** the split.
- **Why:** Expensify, Navan and SAP Concur all keep dollar limits, category lists and MCC rules as configuration. Even Ramp's policy agent, which is LLM-led, layers deterministic rules (dollar limits, blocklists, category restrictions) on top. Almost every decision below follows from this split.

## 2. How the rules are encoded

- **Options:** a Python function per rule; a YAML table read by a generic evaluator; a rules library (GoRules `zen-engine`, `rule-engine`, Cedar, OPA); a mix.
- **Picked:** the mix. `rules.yaml` holds each rule's data (severity, thresholds, `applies_to`, carve-outs and the policy text word for word), and each rule has a small Python check that reads its numbers from it. Rules are evaluated independently, and the disposition (EXC-01) is computed over their results.
- **Why:**
  - **Martin Fowler** notes that a simple rules engine is easy to build yourself, and that chaining, where one rule's action changes another's condition, is what makes rule systems hard to reason about and debug. So no rule triggers another.
  - A library is overkill for about 30 rules, and the older Python ones (`business-rules`, `durable_rules`) haven't been updated since 2022 and 2020. `zen-engine` is the path if it ever has to scale.
  - Cedar's "forbid … unless" is the model for carve-outs such as a pre-approval waiving a cap.
- **As built:** TIME-03 (split transactions) is the one rule that compares receipts at the same merchant, and when it splits a meal cap it replaces that cap, as the regional caps replace MEAL-01. DOC-01 and GIFT-01 test each receipt alone, so they keep anything else they find. `rules.yaml` lists what it may replace, and the trace records what it did.
- **Drafting the rules:** an LLM drafted `rules.yaml` once, offline, and a person reviewed it, as AWS Bedrock Automated Reasoning and Concur's AI rule authoring do. Never at runtime. A test checks every quote against the policy text.

## 3. Classifier

- **Options:** strict JSON-schema output; tool calling; a framework (Instructor, Pydantic AI); embeddings or fine-tuning.
- **Picked:** one structured-output call per receipt, validated in code, returning a category, item kind and recipient for each line. `restricted` is looked up in the taxonomy, so the model can't contradict itself. The policies are not sent.
- **Why:**
  - **Pipal et al. (2026)** tested eight production models on 3,962 expert-coded texts. Six of the eight stayed within 2 points of one-item-per-call accuracy at batches of up to 100, and asking for up to 10 fields per item did no harm. A receipt is about 2.5 lines and three fields, so one call per receipt costs nothing in accuracy.
  - Fine-tuning and nearest-neighbour classifiers need labelled data, and the brief forbids using the labels as input.
  - The engine is code, so the classifier only needs the taxonomy: about 1,000 tokens a call instead of the whole policy set, and accuracy falls as input grows ("context rot").
- **As built:** the client asks for `json_object` output, not strict `json_schema`, so the category list is enforced by validation and one retry, not during decoding.

## 4. Confidence and the EXC-03 threshold

- **Options:** a confidence number the model writes; token logprobs on the category; sampling several times and measuring agreement; a second verifier call; Jev's built-in probabilities.
- **Picked:** the probability of the category's tokens, with `category` before `reason` in the reply and reasoning turned off, so the probability measures the answer rather than a justification. The threshold is 0.5, set by a rule fixed before the dev set ran (NOTES.md).
- **Why:**
  - **Ramp's engineering team** found LLMs give a confidence of 70–80% almost whatever the case, and research on verbalised confidence agrees it is sparse and overconfident.
  - Sampling multiplies cost, and a verifier call can disagree with the first and needs reconciling.
  - The threshold stays low because every EXC-03 signal is scored as wrong, and choosing it from the answer key would be using labels as input.
- **As built:** on the first run, at temperature 0, the API returned logprob 0.0 for every chosen token and −9999 for every alternative, so the probabilities say nothing. At temperature 1 they are the model's own distribution: "Moscow Mule mug" comes back `household_goods` at 0.99954, with `other`, gift and alcohol as the next tokens. The classifier ran at temperature 1, and the cache kept reruns identical. On the pinned hosts (section 8) temperature 0 gives real logprobs, so after the freeze [experiment 6](experiments/semantic_first/README.md) tested the switch on every test set against a bar committed first. It passed, with no category or decision on the 32 receipts changed and the dev set's one miss fixed, so the classifier now runs at temperature 0, as the brief asks. Confidences on borderline lines still vary a little between calls at 0; the cache keeps reruns identical.

## 5. Translation

- **Options:** translate to English, then classify; classify the original directly; a translation API (DeepL, NLLB).
- **Picked:** a separate, cached call writes a short English gloss of each non-English line, and the classifier reads the original and the gloss together.
- **Why:**
  - **Google Research (NAACL '24)** found that classifying directly in the source language beat translating to English first in 94 of 108 languages for PaLM2-L. Translating first won only in seven low-resource languages. All six languages here are high-resource, so the classifier reads the original, and the gloss is extra context.
  - A KDD '25 study of classification prompts found that the translation strategy measurably changes accuracy, so it is a design choice, not a default.
  - The brief's Part 3 asks for each failure to be blamed on a stage, translation included. That needs the gloss saved on its own.
  - DeepL or NLLB would add another service that sees the data, for no gain on six widely spoken languages.

## 6. Retrieval

- **Options:** a structured index; BM25 keyword search; small embeddings; passing whole documents.
- **Picked:** a structured index built from each rule's `applies_to` (category, MCC, expense type, region), plus cross-references and the rules that always apply. It is deterministic and logged.
- **Why:** the engine is code, so retrieval decides which checks run. A rule it misses is a violation the engine misses, so it can't be fuzzy. It keeps 9.5 of 34 rules per receipt and missed none.

## 7. Evidence and explanations

- **Options:** an LLM-written explanation with a citation; the policy text stored word for word, with an explanation built from the computed numbers.
- **Picked:** the stored quote and a template, such as "Meal spend on 2026-04-24 is $214.80 per person, over the $75.00 daily cap."
- **Why:** a stored quote can't be invented, and evidence is graded. Ramp's policy agent likewise links each decision to the policy sections it relies on, and Instructor's citation pattern validates a quote against its source the same way.

## 8. Model

| | Claude Haiku 5.5 | DeepSeek V4.1 Flash | Jev 1.13 |
|---|---|---|---|
| Price per M tokens (in / out) | $0.10 / $0.50 | $0.30 / $1.20 | $0.042 / $0 |
| Temperature setting | Not supported | Yes | — |
| Logprobs | No | Yes | Returns probabilities itself |
| Quality signal | Vals index 54.3 | Vals index 61.3; 76% vs Jev's 72.5% in one study | Weak with many classes and non-English text |

- **Picked:** DeepSeek V4.1 Flash, which I pushed for. OpenRouter serves it as `deepseek/deepseek-v4.1-flash`, the DeepSeek-V4.1-Flash-1017 release.
- **Why:**
  - **Artificial Analysis (September 2026, the release before 1017):** with reasoning off it scored 24.7 on the Intelligence Index, the highest of the 187 open-weights non-reasoning models listed, on 44.5M output tokens against 253M with reasoning at max effort. It lists no multilingual or instruction-following score for it, so those were left to the dev set.
  - **Reasoning off, because this data gives it nothing to fix.** All 43 dev items came back right at confidence 0.96 or higher, all 65 receipt items were right, and the CORD misses were taxonomy gaps and one mistranslation, not misreadings. Leaving it off saves cost and time: on Artificial Analysis's index the 1017 release used 3.4 times the output tokens with maximum reasoning (174M against 51M), while here a receipt takes about 1.4 s (31 calls in 42 s), and its 156 output tokens about 0.1 s of that at roughly 1,500 tokens a second. Reasoning should be retested on a labelled sample of real receipts, where harder items may turn up.
  - It returns logprobs and takes a temperature, which the confidence method needs. Haiku 5.5 does neither.
  - Jev returns probabilities for free, but independent reviews put 21 categories, six languages and misleading names that need world knowledge squarely in its weak spots. It had launched six weeks earlier.
  - OpenRouter's Classifiers feature tags whole requests after they complete and returns no confidence, so it can't classify lines or drive EXC-03.
- **As built:** the first run, on the only key available then, used reasoning off at temperature 1: 156 output tokens and $0.00025 a receipt. The submitted run is the same model through OpenRouter, the brief's provider, once its key arrived: 152 output tokens and $0.00019 a receipt, with every category and decision on the 32 receipts unchanged.
- **As built, the OpenRouter host:** left to its own routing, OpenRouter sent the smoke test to CoreWeave, which returned no logprobs although its listing says it supports them and the request required every parameter. Fireworks and Together both returned real ones, so the client pins them in that order with no fallback (`validator/llm.py`, `PINNED`). Fireworks served every call. On these hosts temperature 0 gives real logprobs too, unlike the first run's; the classifier stayed at temperature 1 so the frozen config was unchanged, until experiment 6 tested 0 (section 4). The first run's client and its cached responses were then removed, so the validator calls OpenRouter only; the first run's outputs remain in git history.

## 9. Keyword baseline

- **Picked:** a word list per category, with restricted terms in all six languages and no brand names, frozen before its first run. Its labels go through the same retriever and engine.
- **Why:** the same engine means the comparison isolates the classifier. Its failures were predicted before it ran: it would misfire on beer-battered cod and rum cake, and miss brand names. `docs/evaluation.md` section 5 has the result.

## 10. Evaluation

- **Options:** an LLM-eval framework (promptfoo, DeepEval, Inspect); a plain script.
- **Picked:** a plain `evaluate.py` that scores the committed outputs and writes `metrics.json`, with Wilson 95% intervals, plus a hand-written dev set of trap items for all prompt work.
- **Why:**
  - The frameworks are built for open-ended output. Exact labels need only F1 and counts.
  - **HateCheck (ACL 2021)** showed that held-out accuracy hides specific failures that targeted functional tests expose. The dev set applies that to the traps in this kind of data: dishes named after drinks, misleading merchants, named recipients, laundry that reads like clothing.
  - With 9 clean receipts, intervals are wide: 1 false positive in 9 is anywhere from 2% to 43%.
  - The base rate changes everything. With recall 0.9 and a false-positive rate of 1 in 9, flag precision is 0.92 at this dataset's 57% violation rate, but 0.20 at a real stream's 3%.

## 11. AWS and the viewer

- **AWS:** API Gateway HTTP API with a Cognito JWT authorizer, SQS between two Lambdas, and DynamoDB for the cross-receipt totals. HTTP APIs are cheaper and simpler than REST APIs, and AWS advises against API keys for authentication. The full design and costs are in `docs/aws-design.md`.
- **Viewer:** Streamlit, whose multipage navigation gives the brief's three views in one small app, plus a Review page where a person records corrections for the lookup to learn from (section 12). Gradio is aimed at model input/output demos rather than browsing results.

## 12. Semantic lookup before the model

- **Options:**
  - the model on every line (main);
  - a lookup first, with the model as fallback;
  - a fine-tuned small classifier (no data to train it).
- **Picked:** a local, semi-deterministic semantic lookup first ([`validator/lookup.py`](validator/lookup.py), settings and data in `rules/lookup/`). Every line it is unsure of goes to the model, and the model's unsure lines go to a person (EXC-03). For each line of an itemized receipt:
  1. **Code checks** send the line to the model if it may name a recipient; may be a phone, prescription or event ticket; has no real words; or comes from a package store, pharmacy or supermarket.
  2. **Restricted lines block.** The line goes to the model if it is close to a restricted line a person reviewed, or to one of the library's restricted examples (sentence similarity of at least 0.855), or if it has the same words as one by meaning.
  3. **Every word accounted for, by meaning.** The lookup decides only if some reviewed line accounts for every word of this line, and this line accounts for every word of that one. Two words count as the same if their embeddings are at least τ_word = 0.934 alike. A single unexplained word sends the line to the model.
  4. **Everything else goes to the model.** The lookup never decides a restricted category; it can only say "safe, a person approved these words" or "ask the model".
- **Why this approach:**
  - **Every looser version let look-alikes through.** Matching a whole line by overall similarity called restricted items food whenever they resembled a reviewed line:
    - "Night cream Soybean" via "Cream [L]" (experiment 10);
    - "Arak Bali" via "Nasi Campur ala Bali" (experiment 11);
    - "LONG ISLAND ICED TEA" via "THAI ICED TEA" (experiment 12).
  - **Each trap adds a word that nothing in the reviewed line explains.** Requiring every word on both sides to be accounted for closes that hole, while similarity still accepts typos and size words ("Soaghetti" ≈ "Spaghetti", "LARGE" ≈ "Grande").
  - **It is semi-deterministic.** The lookup gives the same answer for the same line every time, and each decision names the reviewed line it copied. Only the model's half varies.
- **How it was tested (experiment 12, [experiments/semantic_v4](experiments/semantic_v4/README.md)):**
  - **What was committed first:** the plan, the bar and a blind trap set, before the rule ran on any of them. The trap set was written by an agent that saw neither the design, the lookup's memory nor the CORD receipts.
  - **The traps:**
    - **What the lookup did:** it called **no** restricted trap safe: 0 of 57 blind and 0 of 66 from experiment 11, with the memory as shipped and after learning from 1,000 more receipts.
    - **What the previous lookup did:** it let 2 to 4 through in each set.
  - **A replay of 200 CORD receipts:**
    - every one of its 248 decisions was right, with no restricted item called safe;
    - it decided 39% of lines;
    - it was as accurate as the model (605 lines right against 599, the same false alarms and misses).
  - **The project's own sets:** identical to the model.
  - **It failed one bar as written:** exact word matching, with no embeddings, decided slightly more lines (41%) just as safely. So meaning adds little with this embedding model, which puts single words close together ("cerveza" and "lemonade" at 0.92), forcing a strict threshold. It was adopted anyway, as the user's choice: equally safe, a semantic matcher that tolerates spelling, and 2 points fewer lines decided.
- **Why a cold start is fine:**
  - **An empty table sends every line to the model,** which is exactly what main does: the same cost, accuracy and safety.
  - **The lookup only ever removes model calls.** It adds no risk on day one, and its savings grow as reviewed lines arrive.
  - **On the 32 receipts it is cold.** Its memory holds Indonesian café lines from CORD, not English business receipts, so it would decide 1 of 79 lines ("Soup of the day").
- **How the table fills, and why it must be from people:**
  - **Two keys:** a line enters memory only when a person's decision on it (a reviewer resolving a flag or an unsure line, an audit sample, an approver signing off the report) agrees with the model at confidence 0.9 or more.
  - **Lines a person confirms as restricted become restricted references,** which block from then on.
  - **The table never learns from the model alone.** Otherwise it would freeze the model's mistakes: in experiment 12 the model called a body lotion ("MARINA HBL SUSU") food, and memory would then copy that answer forever.
  - **As built, in two steps:**
    1. **The viewer's Review page** ([`viewer/review.py`](viewer/review.py), [`validator/reviews.py`](validator/reviews.py)) queues the lines worth a person's look:
       - lines the model was unsure of (EXC-03);
       - lines a flag or review cites;
       - the lookup's audit sample;
       - lines where the lookup and the model disagree.

       A person picks the right category, says whether they're sure and gives their name. The review is appended to `rules/lookup/reviews.jsonl`, and no result changes.
    2. **`make learn`** ([`validator/learn.py`](validator/learn.py)) applies each new review under the two-key rule, writes `memory.csv` and the new vectors, and logs every outcome in `learn_log.jsonl`. `make learn-dry` shows the outcomes first and writes nothing. `make run` then uses the new memory.
  - **Why learning is a separate step,** not instant on save: a wrong click is caught in the dry run before it decides lines, and every change to the memory is logged and visible in git.
  - **Two safety rules in the learning step:**
    - **A correction removes a wrong memory.** A review contradicting a remembered line (same words) drops it, even if the new label doesn't enter memory, so that line goes back to the model.
    - **Restricted is never turned safe by a review.** A line once confirmed restricted stays a restricted reference; removing one is done by hand.
  - **What it needs:** learning new text needs the embedding model (`uv sync --group semantic`). Without it, `make learn` refuses before changing anything. The rest of the pipeline still runs offline.
  - **The committed lookup has no reviews.** `reviews.jsonl`, `learn_log.jsonl` and `clients/` are git-ignored, so reviews made in a demo can't change the committed results. `make reset-reviews` undoes a demo. A test checks that the committed memory holds only CORD training lines.
- **One table per client, with danger shared:**
  - **Safe memory per client:** a client's staff keep going to the same cafés, hotels, airlines and taxi apps, so their receipts repeat far more within the client than across clients. Experiment 12 shows the effect:
    - on CORD receipts, memory from earlier CORD receipts decided 39% of lines;
    - on the project's English receipts, the same memory decided 1 line.
  - **Separate tables also mean** one client's receipts never decide another's.
  - **Restricted references can be shared across clients,** as item text with no names: once any reviewer confirms "BINTANG PILSENER" is beer, every client should block it, and sharing caution can't cause a miss. Lines that may name a recipient are never stored.
  - **As built:** the default client's memory is `rules/lookup/memory.csv`; another client's is `rules/lookup/clients/<name>/memory.csv` (`make learn CLIENT=<name>`), starting empty. Every client's restricted references are loaded by every other client; safe lines stay with their own client.
- **As built:**
  - **The memory:** the 813 lines and 14 restricted references learned from the reviewed CORD training receipts, R000–R399. Nothing comes from the 32 receipts or the test sets.
  - **τ_word (0.934):**
    - **The floor:** it is set above the most similar restricted/unrestricted word pair in the frozen keyword list, so no drink word stands in for another.
    - **The calibration:** it was checked on 800 CORD receipts, where 2,398 of 2,399 matched pairs agreed. The one that didn't was a known mislabel.
  - **Offline replay:** `rules/lookup/vectors.npz` holds every sentence and word vector the pipeline needs, so the pipeline replays offline without the embedding model. Any line or word missing from it goes to the model.
  - **It decides** (`mode: decide` in `rules/lookup/config.yaml`):
    - **The 32 receipts:** it decides 1 line ("Soup of the day", from a reviewed "Soup Of The Day"), and every verdict, exception and category is the same as with the model alone (28/28). It's a cold start here: its memory holds CORD's Indonesian café lines.
    - **CORD validation:** it decides 80 of 270 lines, all 80 matching the labels, with no restricted line called safe. The system gets 255 lines right with 10 false alarms, against the model alone's 252 and 12, and 23 of 100 receipts need no model call. Those receipts were in τ_word's calibration set, so this is not an independent test.
  - **Three safeguards come with deciding:**
    - **A second opinion:** whenever any line of a receipt goes to the model, the model reads the whole receipt, so its answer for the lookup's lines is kept in the trace (`model_said`). On CORD it disagreed 3 times out of 43, and the lookup was right each time: the model called two food lines and a service charge `other`.
    - **An audit sample:** a fixed, repeatable 2% of the lookup's decisions (`audit_rate`, chosen by a hash of the line) are marked in the trace for a person to spot-check. The decision stands meanwhile. On CORD that was 1 line, which was right.
    - **A kill switch:** `mode: shadow` makes the lookup decide nothing. It records what it would decide, and the model decides every line, giving exactly the model's answers (a test checks this on the 32 receipts).
  - **The lookup report** (`make lookup-report`, [`evaluation/lookup_report.py`](evaluation/lookup_report.py), `outputs/lookup_report.json`) lists every lookup answer against the labels, the model's second opinion and the audit sample. Its checks, fixed before any client data:
    1. no lookup answer calls a line safe that a person labelled restricted;
    2. at least 99.5% of lookup answers checked against a label agree with it;
    3. every disagreement with the model and every audit line is looked at by a person;
    4. τ_word recalibrated on the client's own reviewed receipts.

    If 1 or 2 fails, switch to shadow mode. Here 1 to 3 hold; 4 can't until a client's reviews exist, so τ_word is still CORD's.
  - **Why decide now, rather than wait in shadow mode:** the user's choice. The evidence is that it let no trap through (0 of 123), it was right on every line it decided on CORD, and every limit stays: the restricted block, the code checks, two-key memory, human review of the model's unsure lines, the audit sample and the kill switch.
  - **Recalibration:** [`validator/calibrate.py`](validator/calibrate.py) takes a client's reviewed lines and applies experiment 12's rule unchanged.
    - **On CORD's 2,079 sure lines** it reproduces τ_word = 0.934288 exactly.
    - **It refuses, and writes nothing,** with fewer than 1,000 sure reviewed lines or 300 matched pairs.
    - **What it never does:** change `mode`, or touch the restricted block, which is a margin of caution that may be lowered but never raised without its own evidence.
- **History:** experiments 6–12 ([experiments/README.md](experiments/README.md)).
  - **6:** a hosted embedding model with hand-written examples decided 30% of lines, but wasn't repeatable.
  - **7:** local and repeatable.
  - **8:** near-repeats and two keys, 54–56% of lines on fresh receipts.
  - **9 and 10:** stricter same-item rules were safe but decided too little, and the near-repeat lookup called a skincare product food.
  - **11:** a version 3.3 failed narrowly, and the traps showed the near-repeat lookup calling alcoholic drinks food.
  - **12:** this rule.
- **Before production:** per client:
  1. start with an empty memory and the shared restricted references, so every line goes to the model;
  2. fill memory by two keys from that client's reviews;
  3. recalibrate τ_word on those reviews (`python -m validator.calibrate --reviewed … --client-reviewed --write`);
  4. watch `make lookup-report` and the audit sample, and switch to `mode: shadow` if a check fails.

## Sources

**Industry practice and the engine**
- [Ramp: how to build agents users can trust](https://builders.ramp.com/post/how-to-build-agents-users-can-trust) · [Ramp Policy Agent](https://support.ramp.com/policy-agent-overview)
- [Expensify workspace rules](https://help.expensify.com/articles/new-expensify/workspaces/Workspace-Rules) · [Navan Audit Engine](https://navan.com/blog/audit-engine) · [SAP Concur audit rules](https://www.sap.com/products/spend-management/concur-expense-configuration-for-audit-rules.html)
- [Martin Fowler: Rules Engine](https://martinfowler.com/bliki/RulesEngine.html) · [Cedar authorization](https://docs.cedarpolicy.com/auth/authorization.html) · [GoRules decision tables](https://docs.gorules.io/learn/authoring/decision-tables)
- [Bedrock Automated Reasoning checks](https://aws.amazon.com/about-aws/whats-new/2025/08/automated-reasoning-checks-amazon-bedrock-guardrails) · [P2T policy-to-DSL (arXiv 2512.04408)](https://arxiv.org/abs/2512.04408)
- [Instructor: validating citations](https://python.useinstructor.com/blog/2023/11/18/validate-citations/)

**Classifier, confidence and translation**
- [Pipal et al. 2026: batching LLM annotation](https://arxiv.org/abs/2604.03684) · [Context rot summary](https://www.zenml.io/llmops-database/context-rot-evaluating-llm-performance-degradation-with-increasing-input-tokens)
- [Verbalized confidence study](https://arxiv.org/abs/2608.04899) · [Confidence protocol sensitivity](https://arxiv.org/abs/2605.27752) · [Self-consistency audit](https://arxiv.org/pdf/2607.08065)
- [Google Research: pre-translation vs direct inference](https://research.google/blog/pre-translation-vs-direct-inference-in-multilingual-llm-applications/) · [How and where to translate (KDD '25)](https://arxiv.org/abs/2507.22923)
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)

**Models**
- [Artificial Analysis: DeepSeek V4.1 Flash, non-reasoning (archived 2026-09-25)](https://web.archive.org/web/20260925000203/https://artificialanalysis.ai/models/deepseek-v4-1-flash-non-reasoning) · [Vals: DeepSeek V4.1 Flash](https://www.vals.ai/models/deepseek_deepseek-v4.1-flash-1017) · [Vals: Claude Haiku 5.5](https://www.vals.ai/models/anthropic_claude-haiku-5-5)
- [Jev after eight days of independent tests (DEV)](https://dev.to/gde/jev-after-eight-days-of-independent-tests-level-with-mid-price-llms-behind-the-frontier-1kln) · [Vals: independent evaluation of Jev](https://www.vals.ai/blogs/independent-evaluation-of-jev) · [TypeSafe confidence docs](https://docs.typesafe.ai/confidence.md)

**Evaluation**
- [HateCheck (ACL 2021)](https://arxiv.org/abs/2012.15606) · [statsmodels proportion_confint](https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html)

**AWS and the viewer**
- [API Gateway: API keys guidance](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-api-usage-plans.html) · [HTTP vs REST APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html) · [Lambda SQS scaling](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html) · [Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)
- [Streamlit st.navigation](https://docs.streamlit.io/develop/api-reference/navigation/st.navigation)
