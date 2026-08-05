# Manual triage baseline methodology

The benchmark compares swarm end-to-end wall-clock time against a
**documented per-step manual triage baseline**. This is not a controlled study
of human engineers; it is a transparent estimate whose numbers you can
recompute and challenge.

## How the baseline was timed

One engineer performed the following steps once per scenario against the same
seeded fixtures the swarm uses, with a stopwatch. Times are wall-clock seconds
and include tool wait time (Prometheus UI, GitHub commit list, reading diffs).

| Step | What a human does | Observed range (s) | Value used (s) |
| --- | --- | --- | --- |
| 1. Notice the page | Open Grafana/Prometheus, find the error-ratio panel, confirm it is real | 60–120 | 90 |
| 2. Establish when | Scrub the graph to the first inflection; note the timestamp | 45–90 | 60 |
| 3. Pull commits | Open the service repo, filter commits around that window | 90–180 | 120 |
| 4. Rank suspects | Read 3–5 commit messages + diffs; pick a top suspect | 180–360 | 240 |
| 5. Propose a fix | Decide which knob/file to change and draft the change | 120–240 | 180 |
| 6. Validate somehow | Run the service locally or in a throwaway container and hit health/checkout | 180–360 | 240 |

Sum of the "value used" column: **930 seconds** (~15.5 minutes).

Per-scenario baselines in `scenarios.py` adjust this by ± a few minutes when
the seeded evidence is denser or thinner (see each scenario's
`manual_baseline_seconds`).

## What is deliberately excluded

- Time spent waiting for a human to be paged / join a bridge
- Cross-team coordination
- Writing the postmortem

Including those would inflate the baseline and overstate the swarm's relative
gain. The comparison is intentionally scoped to the same work the swarm does:
detect → bisect commits → propose → validate.

## Reproducing

```bash
# regenerate fixtures and seed Prometheus
python -m benchmark.seed_prometheus --all

# run the harness (uses heuristic Ops planner unless ANTHROPIC_API_KEY is set)
python -m benchmark.benchmark

# inspect
cat benchmark/benchmark_results.md
```

Re-running the script regenerates the report from measured swarm times and the
baselines above. The reduction percentage is whatever the math produces — it
is not hardcoded.
