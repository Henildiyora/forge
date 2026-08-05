# Benchmark results

Generated: `2026-08-05T02:54:16.145137+00:00`

> Benchmark scenarios use seeded Prometheus data and fixture commit histories — not production traffic. Manual baselines are documented per-step estimates, not a controlled human study.

## Summary

- Scenarios: **3**
- Average manual triage baseline: **860.0s**
- Average swarm end-to-end: **1.5s**
- Average reduction: **99.8%**

Reduction is recomputed from this run's measurements. See `baseline_methodology.md` for how the manual numbers were obtained.

## Per scenario

| Scenario | Manual (s) | Swarm (s) | Reduction | Status | Dry-run |
| --- | ---: | ---: | ---: | --- | --- |
| feature_flag_blowup | 840 | 1.5 | 99.8% | ready_to_apply | pass |
| payment_timeout | 960 | 1.5 | 99.8% | ready_to_apply | pass |
| retries_zeroed | 780 | 1.6 | 99.8% | ready_to_apply | pass |

## Notes

### feature_flag_blowup

FEATURE_CHECKOUT_V2 flipped on without backend support, spiking 5xx.

- run_id: `fbe9719fa06d`
- nodes: monitoring_agent → code_analysis_agent → ops_agent → dry_run_validate → ready_to_apply
- top commit: `b2c3d4e5f607`
- fix: Restore misconfigured knobs for checkout-api (FEATURE_CHECKOUT_V2)

### payment_timeout

PAYMENT_TIMEOUT_MS was lowered to 50ms by commit a1b2c3d4, causing a 5xx error-ratio spike on checkout-api.

- run_id: `d0e09716b3a9`
- nodes: monitoring_agent → code_analysis_agent → ops_agent → dry_run_validate → ready_to_apply
- top commit: `a1b2c3d4e5f6`
- fix: Restore misconfigured knobs for checkout-api (PAYMENT_TIMEOUT_MS)

### retries_zeroed

MAX_RETRIES set to 0, turning transient upstream blips into 5xx.

- run_id: `d91556c633f1`
- nodes: monitoring_agent → code_analysis_agent → ops_agent → dry_run_validate → ready_to_apply
- top commit: `c3d4e5f60718`
- fix: Restore misconfigured knobs for checkout-api (MAX_RETRIES)
