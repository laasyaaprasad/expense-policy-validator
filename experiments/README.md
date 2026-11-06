# Experiments

Alternatives tried after the config was frozen. Each has a plan written before its first run, its own results and spend, and changes nothing in the submitted system or its scores.

| # | Experiment | Question | Result |
|---|---|---|---|
| 1 | [Cascade classifier](cascade/README.md) | Can a cheap embedding stage handle the easy lines and escalate only uncertain ones to the LLM? | Same accuracy as LLM only, but it escalated 81% of receipt lines (9% cheaper) and 99% of real ones, and it turned one uncertain real line into a false flag |
