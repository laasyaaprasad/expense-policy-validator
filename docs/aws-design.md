# Running the validator on AWS (on paper)

**Architecture:** caller → API Gateway → ingest Lambda → SQS → lookup Lambda → worker Lambda (the five pipeline stages) → DynamoDB and S3.
- **API: API Gateway HTTP API** (cheaper than REST APIs, with a built-in JWT authorizer): `POST /receipts` returns `202` and an ID, `GET /receipts/{id}` the result.
- **Compute: ingest and worker Lambdas with SQS between them**, so callers never wait on the model; worker concurrency stays under the rate limit; failures go to the dead-letter queue.
- **Semantic lookup: its own Lambda** (a container image with the pinned open embedding model, 4 GB), so the worker isn't billed at 4 GB while it waits on the model. It decides the lines it can and passes the rest on; a receipt it decides whole never reaches the model. Its library and reviewed-line memory are versioned files, released like `rules.yaml`.
- **State: DynamoDB** keeps cross-receipt totals (meal days, trip OTC, baggage, same-merchant days), written once per receipt ID so redeliveries aren't double-counted; a receipt that tips a day over a cap re-decides the earlier ones. Results go to S3.
- **Logs: CloudWatch Logs**, one trace per receipt, with no line text or names.

**Policy documents.** Policies and `rules/rules.yaml` live in git; a change is a reviewed pull request editing both. CI checks each quote and reruns the oracle and synthetic suites; the merge deploys. Each decision records its policy, model and prompt versions.

**Authentication and secrets.** Callers use Cognito client-credentials tokens (one client each, read or write scope), each reused for its hour since Cognito bills per token (one per request: $6,750 a month). The OpenRouter key sits in Secrets Manager, readable only by the worker, never in code or logs.

**Cost at 50,000 receipts a day:** 50,000 × 30.4 = 1.52M receipts a month, at the final run's measured tokens per receipt.

| Line | Arithmetic | $/month |
|---|---|---|
| Model (OpenRouter, Fireworks) | 0 cached + 952 uncached + 141 output tokens a receipt on average (the lookup decided 4 of the 32 whole), billed at $0.000169 per receipt × 1.52M | ≈ 257 |
| Lookup Lambda | 1.52M × 0.3 s (0.17 s measured on a laptop CPU, assumed slower on Lambda) × 4 GB × $0.0000166667 | ≈ 30 |
| Worker Lambda | 1.52M × 1.4 s (measured) × 0.5 GB × $0.0000166667 | ≈ 18 |
| API, SQS, DynamoDB, S3, logs | 3M requests, 4.5M messages, 1.52M PUTs, 3 GB of logs | ≈ 19 |
| Cognito tokens | 10 callers × 720 tokens (one an hour) × $0.00225 | ≈ 16 |

**About $340 a month**, 76% of it the model, as billed after Fireworks' prompt caching; at list price ($0.21 / M in, $0.84 / M out) the model line would be $484. At this volume the lookup costs about what it saves on the 32 receipts. It saves more as its memory of reviewed receipts grows: on 100 real CORD receipts it decided 37 whole, which would put the model line near $175.

**Before production.**
1. **Watch the model.** Alarm on errors and latency; chart dispositions, rule hits and confidence; log reviewer verdicts for flag precision; rerun the dev and gold gates on upgrades.
2. **Fail safe.** Retry with backoff; unclassifiable receipts go to **review, never pass**.
3. **Protect personal data.** Agree retention terms or move to Bedrock (re-validated) before real receipts reach an outside API; encrypt with KMS; expire line text once audited.
