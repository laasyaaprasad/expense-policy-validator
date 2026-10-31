# Running the validator on AWS (on paper)

**Flow:** caller → API Gateway (HTTP API, JWT authorizer) → ingest Lambda → SQS (+ dead-letter queue) → worker Lambda (translate → classify → retrieve → engine → report) → DynamoDB and S3. Logs and metrics go to CloudWatch.

## Architecture

- **API: API Gateway HTTP API**, cheaper and simpler than REST APIs, with a built-in JWT authorizer. `POST /receipts` validates the record, queues it and returns `202` with an ID; `GET /receipts/{id}` returns the result.
- **Compute: two Lambdas with SQS between them**, so callers never wait on the model. SQS absorbs bursts, the worker's maximum concurrency keeps calls under the provider's rate limit, and failures go to a dead-letter queue.
- **State: DynamoDB, which the engine needs.** MEAL-01 and REG-02/03 add up spend per employee per day, HEALTH-01 per trip. The worker keeps those totals in aggregate items (`employee#date`, `trip#id`) and decides against them. When a later receipt pushes a day over a cap, as the split pair in the data does, it re-decides the earlier receipts on that key and stores a new result version. Result records go to S3.
- **Logs: CloudWatch**, structured JSON (receipt ID, stage, latency, tokens, cost), no line text or names, 30-day retention.

## The policy documents

The policies and `rules/rules.yaml` live in git with the code. A policy change is a pull request that edits the document and `rules.yaml` together (an LLM may draft the YAML offline; a person reviews it). CI runs the existing gates: the verbatim-evidence test fails if the YAML no longer quotes the document exactly, and the oracle and synthetic suites show what the change does to known answers. The merge deploys. Every decision records `policy_version` (a hash of `rules.yaml`), model and prompt version, so an audit can replay it. AppConfig is the upgrade path if policies must change without a deploy.

## Authentication and secrets

- **Callers** get OAuth client-credentials tokens from Amazon Cognito, one app client per calling system; the JWT authorizer checks token and scope (`receipts/write`, `receipts/read`). AWS advises against API keys for authentication.
- **The model API key** sits in Secrets Manager under KMS, readable only by the worker's IAM role, fetched once per cold start and held in memory. It never appears in code, environment files or logs.

## Cost at 50,000 receipts a day

That is 50,000 × 30.4 = **1.52M receipts a month**. Model tokens are the averages measured on the v1 run (`outputs/v1/metrics.json`, `usage`): 31 classification calls and 6 translation calls over 32 receipts.

| Line | Arithmetic | $ / month |
|---|---|---|
| Model (DeepSeek V4.1 Flash, peak prices) | (852 cached × $0.006 + 199 uncached × $0.30 + 156 output × $1.20) / 1M = **$0.000252 per receipt** × 1.52M | **≈ 383** |
| … if no prompt-cache hits | (1,051 × $0.30 + 156 × $1.20) / 1M = $0.000502 × 1.52M | ≈ 764 |
| Worker Lambda | 1.52M × 1.5 s (measured: 31 calls in 42 s) × 0.5 GB × $0.0000166667 + 1.52M × $0.20 / M | ≈ 19 |
| API Gateway HTTP API | 3.0M requests (submit + fetch) × $1.00 / M | ≈ 3 |
| SQS | 3 requests per receipt × 1.52M × $0.40 / M | ≈ 2 |
| DynamoDB on demand | ~3 writes and ~3 reads per receipt | ≈ 4 |
| S3, CloudWatch Logs, Secrets Manager | 1.52M PUTs ≈ $8; about 2 KB of log per receipt ≈ 3 GB ≈ $1.5; one secret ≈ $0.40 | ≈ 10 |

The total is **about $420 a month**, about 90% of it model calls. Cognito's charge for machine clients is extra and not priced here. The 81% cached share comes from the shared system prompt and holds only while traffic is steady, hence the no-cache line.

## Before production

1. **Watch the model, not just the service.** Alarm on dead-letter depth, errors and p95 latency, and on tokens per receipt and the flag and review rates: a shift there is the first sign of a model change or a new receipt pattern. Pin the model version and rerun the dev and gold gates before any upgrade.
2. **Fail towards review.** Retry with backoff and jitter, let SQS buffer an outage, redrive from the dead-letter queue. A receipt that cannot be classified goes to **review, never pass**. A second provider is a fallback only once it passes the same gates, since the EXC-03 threshold rests on DeepSeek's logprobs.
3. **Protect personal data.** Receipts name employees, clients and officials, and today they go to DeepSeek's own API. Before real data does, agree retention terms with the provider or move to a model hosted in AWS (Bedrock), re-validated the same way. Encrypt S3 and DynamoDB with KMS, use CloudWatch Logs data protection, and expire line text after the audit period.
