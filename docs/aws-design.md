# Running the validator on AWS (on paper)

**Flow:** caller → API Gateway → ingest Lambda → SQS (+ dead-letter queue) → worker Lambda (translate → classify → retrieve → engine → report) → DynamoDB and S3.

## Architecture

- **API Gateway HTTP API**, cheaper than REST APIs and with a built-in JWT authorizer. `POST /receipts` returns `202` and an ID; `GET /receipts/{id}` returns the result.
- **Two Lambdas with SQS between them**, so callers never wait on the model. Worker concurrency stays under the rate limit, and failures go to the dead-letter queue.
- **DynamoDB** holds every cross-receipt total (meal days, trip OTC, baggage allowances, same-merchant days), written once per receipt ID, so a duplicate delivery isn't counted twice; a receipt that tips a day over a cap re-decides the earlier ones. Results go to S3.
- **CloudWatch Logs**, Lambda's default: one trace per receipt, with no line text or names.

## The policy documents

The policies and `rules/rules.yaml` live in git; a policy change is a reviewed pull request editing both. CI checks each quote and reruns the oracle and synthetic suites, and the merge deploys. Every decision records the policy, model and prompt versions.

## Authentication and secrets

Callers get client-credentials tokens from Cognito (one client each, read or write scope) and reuse each for its hour: Cognito bills every token; one per request would cost $6,750 a month. The model key is in Secrets Manager, readable only by the worker, never in code or logs.

## Cost at 50,000 receipts a day

50,000 × 30.4 = 1.52M receipts a month, at the final run's measured tokens per receipt.

| Line | Arithmetic | $/month |
|---|---|---|
| DeepSeek V4.1 Flash via OpenRouter (Fireworks), as billed | 0 cached + 1050 uncached + 152 output tokens; billed at $0.21 / M in (often less after Fireworks' own prompt caching) and $0.84 / M out = $0.000193 per receipt × 1.52M | ≈ 294 |
| Worker Lambda | 1.52M × 1.4 s (measured) × 0.5 GB × $0.0000166667 | ≈ 18 |
| API Gateway, SQS, DynamoDB, S3, logs | 3M requests, 4.5M messages, 1.52M PUTs, 3 GB of logs | ≈ 19 |
| Cognito tokens | 10 callers × 720 tokens (one an hour) × $0.00225 | ≈ 16 |

**About $350 a month**, 85% of it the model; at Fireworks' list price with no prompt caching that line rises to $529.

## Before production

1. **Watch the model.** Alarm on errors and latency; chart dispositions, rule hits, confidence. Log reviewer verdicts for flag precision; rerun the dev and gold gates on upgrades.
2. **Fail safe.** Retry with backoff; unclassifiable receipts go to **review, never pass**.
3. **Protect personal data.** Before real receipts reach an outside API, agree retention terms or move to Bedrock (re-validated); encrypt with KMS and expire line text once audited.
