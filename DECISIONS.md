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
- **As built:** DeepSeek's own API offers `json_object` output but not `json_schema`, so the category list is enforced by validation and one retry, not during decoding.

## 4. Confidence and the EXC-03 threshold

- **Options:** a confidence number the model writes; token logprobs on the category; sampling several times and measuring agreement; a second verifier call; Jev's built-in probabilities.
- **Picked:** the probability of the category's tokens, with `category` before `reason` in the reply and reasoning turned off, so the probability measures the answer rather than a justification. The threshold is 0.5, set by a rule fixed before the dev set ran (NOTES.md).
- **Why:**
  - **Ramp's engineering team** found LLMs give a confidence of 70–80% almost whatever the case, and research on verbalised confidence agrees it is sparse and overconfident.
  - Sampling multiplies cost, and a verifier call can disagree with the first and needs reconciling.
  - The threshold stays low because every EXC-03 signal is scored as wrong, and choosing it from the answer key would be using labels as input.
- **As built:** at temperature 0, DeepSeek returns logprob 0.0 for every chosen token and −9999 for every alternative, so the probabilities say nothing. At temperature 1 they are the model's own distribution: "Moscow Mule mug" comes back `household_goods` at 0.99954, with `other`, gift and alcohol as the next tokens. The classifier runs at temperature 1, and the cache keeps reruns identical.

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

- **Picked:** DeepSeek V4.1 Flash, which I pushed for. DeepSeek's API serves it as `deepseek-flash`, now the DeepSeek-V4.1-Flash-1017 release.
- **Why:**
  - **Artificial Analysis (September 2026, the release before 1017):** with reasoning off it scored 24.7 on the Intelligence Index, the highest of the 187 open-weights non-reasoning models listed, on 44.5M output tokens against 253M with reasoning at max effort. It lists no multilingual or instruction-following score for it, so those were left to the dev set.
  - **Reasoning off, because this data gives it nothing to fix.** All 43 dev items came back right at confidence 0.96 or higher, all 65 receipt items were right, and the CORD misses were taxonomy gaps and one mistranslation, not misreadings. Leaving it off saves cost and time: on Artificial Analysis's index the 1017 release used 3.4 times the output tokens with maximum reasoning (174M against 51M), while here a receipt takes about 1.4 s (31 calls in 42 s), and its 156 output tokens about 0.1 s of that at roughly 1,500 tokens a second. Reasoning should be retested on a labelled sample of real receipts, where harder items may turn up.
  - It returns logprobs and takes a temperature, which the confidence method needs. Haiku 5.5 does neither.
  - Jev returns probabilities for free, but independent reviews put 21 categories, six languages and misleading names that need world knowledge squarely in its weak spots. It had launched six weeks earlier.
  - OpenRouter's Classifiers feature tags whole requests after they complete and returns no confidence, so it can't classify lines or drive EXC-03.
- **As built:** first on DeepSeek's own API, the only key available then, with reasoning off at temperature 1: 156 output tokens and $0.00025 a receipt. The submitted run is the same model through OpenRouter, the brief's provider, once its key arrived: 152 output tokens and $0.00019 a receipt, with every category and decision on the 32 receipts unchanged.
- **As built, the OpenRouter host:** left to its own routing, OpenRouter sent the smoke test to CoreWeave, which returned no logprobs although its listing says it supports them and the request required every parameter. Fireworks and Together both returned real ones, so the client pins them in that order with no fallback (`validator/llm.py`, `PINNED`). Fireworks served every call. On these hosts temperature 0 gives real logprobs too, unlike DeepSeek's API; the classifier stays at temperature 1 so that the frozen config is unchanged.

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
- **Viewer:** Streamlit, whose multipage navigation gives the brief's three views in one small app. Gradio is aimed at model input/output demos rather than browsing results.

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
