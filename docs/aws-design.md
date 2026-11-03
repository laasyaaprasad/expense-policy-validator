# Running the validator on AWS (on paper)

**Flow:** caller → API Gateway → ingest Lambda → SQS (+ dead-letter queue) → worker Lambda (translate → classify → retrieve → engine → report) → DynamoDB and S3.

## Architecture

- **API Gateway HTTP API**, cheaper than REST APIs and with a built-in JWT authorizer. `POST /receipts` returns `202` and an ID; `GET /receipts/{id}` returns the result.
- **Two Lambdas with SQS between them**, so callers never wait on the model. Worker concurrency stays under the rate limit, and failures go to the dead-letter queue.
- **DynamoDB** holds running totals for meal caps and HEALTH-01, keyed `employee#date` and `trip#id` and written once per receipt ID, so a duplicate delivery isn't counted twice; a receipt that tips a day over a cap re-decides the earlier ones. Results go to S3.
- **CloudWatch Logs**, Lambda's default: one trace per receipt, with no line text or names.

## The policy documents

The policies and `rules/rules.yaml` live in git; a policy change is a reviewed pull request editing both. CI checks each quote and reruns the oracle and synthetic suites, and the merge deploys. Every decision records the policy, model and prompt versions.

## Authentication and secrets

Callers get client-credentials tokens from Cognito (one client each, read or write scope) and reuse each for its hour: Cognito bills every token; one per request would cost $6,750 a month. The model key is in Secrets Manager, readable only by the worker, never in code or logs.

## Cost at 50,000 receipts a day

50,000 × 30.4 = 1.52M receipts a month, at the final run's measured tokens per receipt.

| Line | Arithmetic | $/month |
|---|---|---|
| DeepSeek V4.1 Flash, peak prices | (852 cached × $0.006 + 199 uncached × $0.30 + 156 output × $1.20) / 1M = $0.000252 per receipt × 1.52M | ≈ 383 |
| Worker Lambda | 1.52M × 1.5 s (measured) × 0.5 GB × $0.0000166667 | ≈ 19 |
| API Gateway, SQS, DynamoDB, S3, logs | 3M requests, 4.5M messages, 1.52M PUTs, 3 GB of logs | ≈ 19 |
| Cognito tokens | 10 callers × 720 tokens (one an hour) × $0.00225 | ≈ 16 |

**About $440 a month**, nearly 90% of it the model; with no cache hits that line doubles to $764.

## Before production

1. **Watch the model.** Alarm on errors and latency; chart dispositions, rule hits, confidence. Log reviewer verdicts for flag precision; rerun the dev and gold gates on upgrades.
2. **Fail safe.** Retry with backoff; unclassifiable receipts go to **review, never pass**.
3. **Protect personal data.** Before real receipts reach an outside API, agree retention terms or move to Bedrock (re-validated); encrypt with KMS and expire line text once audited.
