# Design decisions — Expense Policy Validator

Researched 2026-10-30. Options and recommendations for the decisions the brief leaves to us. Model prices and capabilities were checked against OpenRouter's live `/api/v1/models/{id}/endpoints` data on that date; everything else is cited below.

**Overall:** the brief matches how the industry works. Expense tools like Expensify, Navan, SAP Concur and even Ramp's AI policy agent use AI to read and classify receipts. Dollar limits, category lists and MCC rules stay as deterministic configuration. Almost every recommendation below follows from that split.

---

## 1. Policy engine: how the rules are encoded

- **Options:** a Python function per rule; a YAML rule table read by a generic evaluator; a rules library (GoRules `zen-engine`, `rule-engine`, Cedar, OPA); or a mix.
- **Recommendation: the mix.**
  - `rules.yaml` holds the data for each rule: `id`, `doc`, `severity`, thresholds, categories and MCCs, `overrides`, `requires_evidence`, `applies_to`, and the policy text word for word.
  - Each rule has a small Python check function, keyed by its ID, that reads its numbers from the YAML.
  - The engine runs in this order:
    1. Build cross-receipt totals first (meal spend per employee per date, OTC spend per trip), then apply regional overrides, so a regional rule replaces the global one.
    2. Evaluate every rule independently. No rule triggers another.
    3. Record each carve-out explicitly, e.g. `waived_by: baggage_delay_ref`.
    4. Compute EXC-01 (disposition) as a pure function over the list of exceptions.
    5. EXC-03 adds review signals, never exceptions.
  - Use `Decimal` for money, one `to_usd()` function, and sorted output so results are always in the same order.
- **Why:**
  - Martin Fowler supports a small hand-written engine for a narrow domain. The main risk he names is rules triggering each other, which step 2 avoids.
  - A library is overkill for about 30 rules. `zen-engine` is the path if it ever has to scale. `business-rules` and `durable_rules` haven't been updated since 2022 and 2020.
  - Cedar's "forbid … unless" is a useful mental model for carve-outs.
- **Rule drafting:** an LLM can draft `rules.yaml` once, offline, from the 9 documents; you review it and commit it. AWS Bedrock Automated Reasoning and Concur's AI rule authoring work this way. Never do it at runtime. Keep the drafting prompt in the repo.
- **Test:** every rule ID in the Markdown appears in the YAML.

## 2. Classifier design

- **Options:** strict JSON schema output; tool calling; Instructor or Pydantic AI; embeddings or fine-tuning.
- **Recommendation: strict JSON schema output.**
  - Use the plain OpenAI SDK pointed at OpenRouter, with `response_format` set to a strict `json_schema` and `category` as an enum of the 21 labels. Set `provider.require_parameters: true` and validate the result with Pydantic.
  - Set `restricted` by looking the category up in the taxonomy in code, so the model can never contradict itself.
  - Also extract a `recipient` per line: a name, and a type of client, employee, government official, or none. The gift rules need it, and it exists only in the line text.
  - And an `item_kind` (entertainment venue, mobile phone, prescription medication, or none). The taxonomy classes these as `other`, but ENT-01, TECH-03 and HEALTH-01 need to tell them apart.
  - A non-itemized line is `other` by rule. Set it in code, with no call and no EXC-03.
  - Make one call per receipt (about 2.5 items each). Include the merchant and MCC, but tell the model they're weak evidence: "classify what the item *is*."
  - Give short category definitions with include/exclude rules, plus a few hard examples you write yourself, such as "dish named after a drink → food." Don't paraphrase them from the answer key.
  - **Don't send the policy documents to the classifier.** The engine is code, so the classifier never needs them. Each call is about 6k tokens instead of 190k.
- **Why:**
  - Schema-constrained calls are the standard for a fixed taxonomy.
  - Fine-tuning or nearest-neighbour classifiers need labelled data we aren't allowed to use.
  - A 2026 study found most models lose little accuracy with small batches of items per call.
- **Cheap check:** re-run the misleading items without the merchant name and compare.

## 3. Confidence and the EXC-03 threshold

- **Options:**
  - A confidence number the model writes itself
  - Token logprobs on the category
  - Sampling several times and measuring agreement
  - A second verifier call
  - Jev's built-in probabilities
- **Recommendation: logprobs**, if the pinned endpoint supports them; the DeepSeek endpoints on Google, Fireworks and Together do.
  - Put `category` *before* any reasoning field and turn the model's reasoning off. Otherwise the model has already decided, and the probability comes out near 1.0.
  - Confidence is the probability of the category's tokens.
  - If logprobs aren't available, fall back to a self-reported number with an anchored scale (0.95 = unambiguous, 0.75 = a plausible alternative exists, ≤0.5 = can't tell).
- **Threshold:** keep it low, around 0.5, because every EXC-03 signal is scored as wrong.
  - Choose it **before** looking at results, for example on about 20 items we write ourselves.
  - Report a threshold sweep in the README as analysis after the fact.
  - Picking the threshold from the answer key would mean using the labels as input.
- **Why:** Ramp's engineering team found self-reported LLM confidence clusters around 70–80% whatever the real certainty. Research agrees those numbers are sparse and overconfident. Sampling several times multiplies cost and needs a temperature above 0, which conflicts with reproducibility.

## 4. Translation (the normaliser)

- **Options:** translate first and then classify; classify directly in the original language; a translation API (DeepL, NLLB).
- **Recommendation: two parts.**
  - The normaliser does deterministic cleanup: Unicode normalisation (NFKC), whitespace, and stripping SKU and quantity noise. The language comes from the data's `language` field.
  - A **separate, cached LLM call** returns an English version of each item.
  - The classifier sees both the original and the English version.
- **Why keep it separate:** Part 3 asks us to blame each failure on translation, classification, retrieval or rule logic. That's only possible if the translation output is saved on its own.
  - Google's research found classifying directly in the original language often beats translating first, which is why the classifier gets the original as well.
  - DeepL would add another service that receives the data, for no gain on six widely spoken languages.

## 5. Retriever

- **Options:** a structured index; BM25 keyword search (`bm25s`); small embeddings in numpy; passing whole documents.
- **Recommendation: the structured index.**
  - Build it from the YAML `applies_to` field, which maps category, MCC, expense type and region to rule IDs.
  - Follow cross-references by matching rule IDs with a regex.
  - Keep some rules always on, such as documentation and approval rules.
  - Make it deterministic and log what it returns.
- **Interview framing:** because the engine is code, retrieval means "which checks run, plus fetching the citation text." A rule that retrieval misses is a violation the engine misses, so it can't be fuzzy.
- **Optional:** run BM25 in a test as a sanity check that the index isn't missing rules.

## 6. Exception reporter

- **Recommendation:**
  - `evidence` is the policy text stored word for word when the YAML is built.
  - A unit test checks that every evidence string appears exactly in its source document, ignoring whitespace differences.
  - Explanations come from templates using the computed numbers, e.g. "$142.00 alcohol ÷ 2 attendees = $71.00 > $50.00 cap (ALC-01)."
  - An LLM rewording pass is optional and must never change the rule ID, the evidence or the numbers.
- **Why:** it can't invent a citation, and evidence is one of the things they grade.

## 7. Model choice

Live OpenRouter data, 2026-10-30:

| | Claude Haiku 5.5 | DeepSeek V4.1 Flash | Jev 1.13 |
|---|---|---|---|
| Model ID | `anthropic/claude-haiku-5.5` | `deepseek/deepseek-v4.1-flash` | `typesafe/jev-1.13` |
| Price per M tokens (in / out) | $0.10 / $0.50 | $0.02–0.60 / $0.18–2.40, depending on endpoint | $0.042 / $0 |
| Context | 1M | 1M | 262K |
| Strict JSON output | Yes on Anthropic, Google, Azure (not Bedrock) | Most endpoints | — |
| Temperature setting | **Not supported** | Yes | — |
| Logprobs | No | About 15 endpoints | Returns probabilities itself |
| Quality signal | Vals index 54.3 | Vals index 61.3; 76% vs Jev's 72.5% in one study | Weak with many classes and non-English text |

**Recommendation: DeepSeek V4.1 Flash, pinned to a single endpoint** that supports temperature, strict JSON and logprobs and isn't listed as quantised. Google Vertex, at $0.30 / $1.20, fits.
- Use `provider: {order: ["google-vertex"], allow_fallbacks: false}`, temperature 0, and reasoning turned off or low.
- Log the `provider` field from every response.
- A full run costs roughly $0.10–0.20.
- Pinning is needed because about 40 providers serve this model, many of them quantised (fp4/fp8), and their outputs differ.

**Fallback and one-run comparison: Haiku 5.5.** It's simpler because it's first-party, but it can't run at temperature 0 (say so in the README; reproducibility then comes from the cache plus the recorded model ID) and has no logprobs.

**On large context:** both models have a 1M window, but context isn't what limits this project. The classifier only needs the taxonomy and one receipt, about 6k tokens. Large context helps with the one-time rule-drafting step, where all 9 documents fit in one prompt. What actually separates the models here is temperature, logprobs and consistent outputs from one endpoint.

**Jev: not the main classifier.**
- Independent reviews say 21 categories, six languages, and misleading names that need world knowledge are exactly its weak spots.
- It launched on 2026-09-15: popular, but not proven.
- OpenRouter lists no supported parameters for it, so how to send it a choice question through the OpenRouter key isn't documented.
- If there's spare time, run it on the English versions of the items and compare macro F1 and calibration. It costs a fraction of a cent and makes a good interview talking point.

**OpenRouter Classifiers: not used in the pipeline.** It's a beta feature, announced 2026-07-24, that tags each request *after* it completes, for cost attribution and governance.
- Tags go to logs and the Analytics API (management key needed), not the API response, so the engine can't consume them.
- It classifies whole requests, not line items, and returns no confidence, so it can't drive EXC-03.
- Only a workspace admin can set it up.
- It could tag production traffic in the AWS design, e.g. translate vs classify calls for cost attribution.

**README data-handling line:** OpenRouter, the pinned upstream provider, and our coding assistants.

## 8. Keyword baseline

- **Recommendation:**
  - A word list per category of restricted terms, matched on whole words after lowercasing, NFKC and stripping accents. Japanese text needs substring matching, because word boundaries don't apply.
  - Add translations of the restricted terms.
  - **Freeze the list before looking at results**, and say so.
  - Run the baseline's labels through the *same* engine, so the disposition comparison isolates the classifier. Matched items get confidence 1.0, so the baseline can never trigger EXC-03 review.
- **Expect** it to catch the obvious terms, misfire on "beer-battered cod" and "rum cake", and miss brand names. Be honest about where it wins: speed, cost, determinism, and maybe precision on the obvious cases.

## 9. Evaluation

- **Recommendation: a plain `evaluate.py`** using pandas, sklearn and statsmodels. It reads the cached JSONL and writes `metrics.json` for the viewer. Frameworks like promptfoo, DeepEval and Inspect are built for open-ended LLM output, and we don't need one for exact labels.
- **Macro F1:** `f1_score(y_true, y_pred, average="macro", labels=sorted(set(y_true)), zero_division=0)`. The `labels=` argument is what makes it match the brief's definition; never use `zero_division=np.nan`, which drops categories.
- **Restricted F1:** plain binary `f1_score`, positive = restricted.
- **Parse failures:** map them to `"__invalid__"` rather than dropping them. Show `classification_report` so the small per-category counts are visible.
- **Small sample:** 28 receipts is tiny. Report Wilson 95% intervals with `statsmodels.stats.proportion.proportion_confint(k, n, method="wilson")`. For example, 1 false positive out of 9 gives an interval of 2–43%.
- **Base rate:** precision = recall × p / (recall × p + FPR × (1 − p)), where p is the share of receipts with a violation.
  - With recall 0.9 and a false-positive rate of 1/9, precision is 0.92 at this dataset's 57%. It's 0.30 at 5%, 0.20 at 3% and 0.14 at 2%.
  - At 50k receipts a day and 3%, that's about 1,350 real flags and 5,400 false alarms, so roughly 80% of reviewer time goes on false alarms.
  - Before production we'd need a shadow-mode test on real traffic, measured reviewer time, and a sample enriched with rare violation types.

## 10. AWS design (on paper)

- **Architecture:**
  1. API Gateway **HTTP API** with a JWT authorizer (Cognito, machine-to-machine client-credentials flow).
  2. An ingest Lambda.
  3. SQS with a dead-letter queue. Its maximum-concurrency setting caps load to OpenRouter's rate limit.
  4. A worker Lambda that runs normalise → classify → engine.
  5. Results in S3, with DynamoDB optional for status lookups.
  6. CloudWatch with structured JSON logs, no personal data, and 30-day retention.
- AWS says not to use API keys for authentication, which rules out the simplest option.
- **Policies:** keep the YAML in git. CI validates it against a set of test receipts with known results, and it deploys with the code. Record the policy version, model ID and prompt version on every decision for audit. AppConfig is the upgrade path if policy owners need to ship changes without a deploy.
- **Secrets:** keep the OpenRouter key in Secrets Manager ($0.40 per secret per month plus $0.05 per 10k calls) and cache it in the Lambda. For production, mention Bedrock as an option so data stays inside AWS.
- **Cost:** 50,000 × 30.4 = **1.52M receipts a month**. Model cost per receipt = (input tokens × input price + output tokens × output price) / 1M.

| Line | Arithmetic | $/month |
|---|---|---|
| Haiku 5.5 ($0.10 / $0.50) | 3–6k in + 500 out → $0.00055–0.00085 per receipt × 1.52M | ~840–1,290 |
| DeepSeek V4.1 Flash on Vertex ($0.30 / $1.20) | 3–6k in + 500 out → $0.0015–0.0024 per receipt × 1.52M | ~2,300–3,700 |
| API Gateway HTTP API | 1.52M × $1.00/M | ~1.5 |
| Lambda | 1.52M × 5 s × 0.5 GB = 3.8M GB-s × $0.0000166667 + requests | ~51–63 |
| SQS | 3 requests per receipt × $0.40/M | ~1.8 |
| CloudWatch Logs | about 2 KB per receipt ≈ 3 GB × $0.50/GB | ~1.5 |
| S3 | PUTs + storage | ~8 |

  - Infrastructure is about $80–100 a month, and the model calls dominate.
  - The model that's best for the project isn't the cheapest at scale. That's an honest trade-off to state on the page. Prompt caching and batch pricing (50% off) reduce both.
- **Before production:**
  1. Monitoring: alarms on dead-letter queue depth, error rate, p95 latency, tokens and cost per receipt, and drift in the flag/review rate.
  2. Model outage: retry with backoff and jitter, let SQS buffer, redrive from the dead-letter queue, and fall back to a second model or Bedrock. A receipt that fails goes to **review, never pass**.
  3. Personal data: redact names and card numbers before the LLM call, use CloudWatch Logs data protection, encrypt S3 with KMS, and keep data only briefly.

## 11. Viewer

**Streamlit** (v1.66): one `app.py` with three pages via `st.navigation([st.Page(...), ...])`, or a `pages/` folder. Load the JSONL with `@st.cache_data`, highlight mismatches with `df.style.apply(...)`, and show headline numbers with `st.metric`. Launch it with `streamlit run app.py`. Gradio is aimed more at model input/output demos than at browsing data.

---

## Still unverified (test before building on it)

- Whether logprobs come back usable under strict `json_schema` output on the pinned DeepSeek endpoint. Make one test call.
- Whether `reasoning.effort` none/low works on DeepSeek V4.1 Flash and Haiku 5.5.
- How to call Jev through OpenRouter (its listing shows no supported parameters).
- The Bedrock Haiku 5.5 price came from aggregators; the official table didn't render.

## Sources

**Industry practice and engine**
- [Ramp: how to build agents users can trust](https://builders.ramp.com/post/how-to-build-agents-users-can-trust) · [Ramp Policy Agent](https://support.ramp.com/policy-agent-overview)
- [Expensify workspace rules](https://help.expensify.com/articles/new-expensify/workspaces/Workspace-Rules) · [Navan Audit Engine](https://navan.com/blog/audit-engine) · [SAP Concur audit rules](https://www.sap.com/products/spend-management/concur-expense-configuration-for-audit-rules.html)
- [Martin Fowler: Rules Engine](https://martinfowler.com/bliki/RulesEngine.html) · [Cedar authorization](https://docs.cedarpolicy.com/auth/authorization.html) · [GoRules decision tables](https://docs.gorules.io/learn/authoring/decision-tables)
- [Bedrock Automated Reasoning checks](https://aws.amazon.com/about-aws/whats-new/2025/08/automated-reasoning-checks-amazon-bedrock-guardrails) · [P2T policy-to-DSL (arXiv 2512.04408)](https://arxiv.org/abs/2512.04408)
- [Anthropic: contextual retrieval](https://www.anthropic.com/news/contextual-retrieval) · [Context rot summary](https://www.zenml.io/llmops-database/context-rot-evaluating-llm-performance-degradation-with-increasing-input-tokens)
- [Instructor: validating citations](https://python.useinstructor.com/blog/2023/11/18/validate-citations/)

**Classifier, confidence, translation, models**
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs) · [OpenRouter reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) · [OpenRouter prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching) · [OpenRouter provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging) · [OpenRouter models API](https://openrouter.ai/api/v1/models)
- [Pipal et al. 2026: batching](https://arxiv.org/abs/2604.03684) · [Verbalized confidence study](https://arxiv.org/abs/2608.04899) · [Confidence protocol sensitivity](https://arxiv.org/abs/2605.27752) · [Self-consistency audit](https://arxiv.org/pdf/2607.08065)
- [Google: pre-translation vs direct inference](https://research.google/blog/pre-translation-vs-direct-inference-in-multilingual-llm-applications/) · [How and where to translate (KDD'25)](https://arxiv.org/abs/2507.22923)
- [Vals: DeepSeek V4.1 Flash](https://www.vals.ai/models/deepseek_deepseek-v4.1-flash-1017) · [Vals: Claude Haiku 5.5](https://www.vals.ai/models/anthropic_claude-haiku-5-5)

**Jev**
- [Jev after eight days of independent tests (DEV)](https://dev.to/gde/jev-after-eight-days-of-independent-tests-level-with-mid-price-llms-behind-the-frontier-1kln) · [Vals: independent evaluation of Jev](https://www.vals.ai/blogs/independent-evaluation-of-jev)
- [Jev engineering guide](https://www.aibuilderclub.com/blog/jev-engineering-guide) · [TypeSafe Choice docs](https://docs.typesafe.ai/primitives/choice.md) · [TypeSafe confidence docs](https://docs.typesafe.ai/confidence.md)

**Evaluation**
- [sklearn f1_score](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.f1_score.html) · [statsmodels proportion_confint](https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html) · [HateCheck (ACL 2021)](https://arxiv.org/abs/2012.15606)

**AWS**
- [API Gateway pricing](https://aws.amazon.com/api-gateway/pricing/) · [Lambda pricing](https://aws.amazon.com/lambda/pricing/) · [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/) · [Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/) · [DynamoDB on-demand pricing](https://aws.amazon.com/dynamodb/pricing/on-demand/)
- [API Gateway: API keys guidance](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-api-usage-plans.html) · [HTTP vs REST APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html) · [Lambda SQS scaling](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html) · [AppConfig](https://docs.aws.amazon.com/appconfig/latest/userguide/what-is-appconfig.html) · [Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)

**Viewer**
- [Streamlit st.navigation](https://docs.streamlit.io/develop/api-reference/navigation/st.navigation) · [Streamlit multipage apps](https://docs.streamlit.io/develop/concepts/multipage-apps/overview)
