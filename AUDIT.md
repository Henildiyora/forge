# Audit: existing repo vs. claimed DevOps Swarm architecture

Date: 2026-08-04
Audited commit: `f0ac61b`

## Summary

The repository did not contain the claimed system. It contained **FORGE**, a
terminal-first AI DevOps CLI of roughly 12,500 non-test Python lines whose job
is to scan a repository and generate Dockerfiles, Kubernetes manifests, and
CI/CD pipelines. The git history shows the repo started life as "DevOps Swarm"
(commit `e6c08f4`) and was subsequently rewritten into FORGE (`5ed535f`
onward), which is why the directory is still named `devops-swarm`.

Some genuinely useful primitives survived that rewrite and were reused. Most of
the codebase did not serve any of the claims and was removed.

## Claim-by-claim findings

### Monitoring Agent — partially existed

`forge/agents/watchman/agent.py` queried Prometheus for error rate, p95 latency,
and pod restarts, and it did so over real HTTP. But:

- Detection was fixed-threshold only (`error_rate > 0.05`). There was no
  baseline, no notion of a spike relative to normal, and therefore no way to
  answer "how bad is this incident."
- It produced a `MonitoringSnapshot`, not an `IncidentSignal`. The snapshot had
  no incident start timestamp, so nothing downstream could window on it.
- Confidence was a hardcoded literal: `0.92 if anomalies else 0.97`.

### Code Analysis Agent — did not exist

`forge/agents/librarian/github_client.py` was a real PyGithub wrapper with
typed outputs, but:

- **It was never called from any agent.** The only callers were its own unit
  tests. The `LibrarianAgent` did purely local AST analysis.
- `recent_commits()` called `repo.get_commits()` with no arguments, so it could
  not window commits by time even if something had wanted to.
- There was no relevance scoring of any kind.

### Ops Agent — did not exist

The closest thing was `forge/agents/remediation/fix_evaluator.py`, which scored
fix proposals that were supplied to it from elsewhere. Nothing in the codebase
generated a fix proposal from incident evidence.

### LangGraph orchestration — real, but for a different purpose

This claim held up structurally. `forge/orchestrator/graph.py` built a genuine
`StateGraph` with `add_conditional_edges`, and `deploy_workflow.py` and
`incident_workflow.py` were real sub-graphs. It was not a linear script wearing
a graph costume. However, the graph routed deploy-versus-incident workflows
through Librarian, specialists, and Captain — not the three claimed agents.

### Shared state — real, but wrong shape

`forge/orchestrator/state.py` defined a Pydantic `SwarmState` that genuinely
flowed between nodes. Its fields were deployment-artifact oriented
(`dockerfile`, `k8s_manifests`, `cicd_pipeline`, `sandbox_cluster_id`). None of
`IncidentSignal`, `CommitCandidate`, `ProposedFix`, or `DryRunResult` existed.

### Dry-run validation — not real for fixes

Three separate things were called "dry run," none of which validated a proposed
fix in isolation:

1. `Settings.dry_run_mode`, a global boolean that suppressed writes.
2. `kubectl apply --dry-run=server` in `k8s_specialist/kubectl_client.py`, which
   validates manifest schema, not behavior.
3. `sandbox_tester/agent.py`, which shelled out to a `vcluster` binary at
   `/usr/local/bin/vcluster`. That binary is not installed on this machine, so
   the path was untested in practice.

No mechanism took a proposed fix, applied it somewhere isolated, ran it, and
derived pass/fail from a real exit code.

### Standardized tool-calling schema — no

Pydantic models were used widely and were well-formed, but they were per-module
and unrelated to each other: `CodebaseScanResult`, `MonitoringSnapshot`,
`FixProposal`, `GeneratedArtifacts`, and so on. There was no shared envelope
describing a tool invocation or its result, so agent outputs were not
composable in any enforced way.

### Prometheus and GitHub integrations — real clients, thin usage

Both `PrometheusClient` (httpx) and `GitHubClient` (PyGithub) issued real
network calls. Prometheus was actually wired into Watchman; GitHub was not
wired into anything. Separately, `forge/integrations/prometheus.py`,
`github.py`, `loki.py`, `jira.py`, and `kubernetes.py` were three-line
placeholder stubs with comments like `"expanded in Sprint 5"`.

### Benchmark and the 45% claim — no code at all

There was no benchmark script. Grep found zero occurrences of `benchmark` and
zero occurrences of `45` as a percentage anywhere in the repo. The number had
nothing behind it.

The nearest relative was `forge/core/hardening.py`, a fault-injection suite
producing a `readiness_score`. It measured resilience, not troubleshooting
time, and produced no comparison against a human baseline.

### Anthropic — real and reusable

`forge/core/llm.py` contained an `AnthropicProvider` making real HTTP calls to
`https://api.anthropic.com/v1/messages`, alongside OpenAI, Ollama, and
llama.cpp providers and a deterministic heuristic fallback. The default backend
was `heuristic`, meaning the shipped default made zero LLM calls.

## What was deleted, and why

Everything below served FORGE's artifact-generation product, not the claimed
incident-response swarm.

| Removed | Reason |
| --- | --- |
| `forge/agents/{docker,k8s,cicd,cloud,serverless,platform}_specialist/` | Generate Dockerfiles, manifests, pipelines, cloud inventory. Not incident response. |
| `forge/agents/{manager,captain}/` | CLI narration and review agents. Not one of the three claimed agents. |
| `forge/agents/librarian/ast_analyzer.py` | Local static analysis of a target repo. The claim is commit-history analysis. |
| `forge/agents/remediation/` | Superseded by the Ops Agent and the real dry-run gate. |
| `forge/agents/sandbox_tester/` | vcluster-based; replaced by the Docker sandbox. |
| `forge/cli/` (12 commands) | `build`, `index`, `chat`, `explain`, `doctor`, `setup`, etc. belong to the old product. |
| `forge/api/` | FastAPI server, Slack webhooks, approval routers. Not claimed. |
| `forge/conversation/` | Natural-language intent and deployment-strategy ranking. Not claimed. |
| `forge/core/{builds,hardening,message_bus,events,approvals,checkpoints,workspace,resume,observability,audit,secrets,registry,strategies}.py` | Scaffolding whose only consumers were the modules above. |
| `forge/agents/base.py` | Event-bus agent base class. The swarm shares state through the graph, not a message bus. |
| `forge/integrations/` | Five placeholder stubs, three lines each. |
| `install.sh`, `Formula/forge.rb`, `scripts/{release,record-demo,run_chaos_tests}.sh` | Distribution for the FORGE CLI product. |
| `infrastructure/{k8s,helm}/` | Deployment manifests for the FORGE API server. |
| `docs/*`, `CHANGELOG.md` | Documented the removed product. |
| Corresponding tests | Roughly 50 test files covering the above. |
| `.DS_Store`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` | Committed or stray build junk; now gitignored. |

## What was kept and adapted

| Kept | Became | Change |
| --- | --- | --- |
| `agents/watchman/prometheus_client.py` | `swarm/tools/prometheus.py` | Added instant `query()` and a sync wrapper; same real `/api/v1/query_range` call. |
| `agents/librarian/github_client.py` | `swarm/tools/github.py` | Added `since`/`until` windowing, commit timestamps, and changed-file lists. |
| `core/llm.py` `AnthropicProvider` | `swarm/llm.py` | Kept Anthropic; dropped OpenAI, Ollama, and llama.cpp. Added structured-output tool calling. |
| `core/config.py` `Settings` | `swarm/config.py` | Trimmed from 40 fields to the Prometheus, GitHub, Anthropic, and sandbox fields actually used. |
| `docker-compose.dev.yml`, `infrastructure/docker/prometheus.yml` | `docker-compose.yml`, `infrastructure/prometheus/` | Reduced to Prometheus plus the sandbox target service. |

## Honest limitations of the rebuild

Stated here so the README and any interview answer match the code:

- Benchmark incidents are **seeded fixtures**, not production traffic. Metric
  data is backfilled into a real Prometheus TSDB with `promtool` and then read
  back over the real HTTP API, so the query path is genuine while the data is
  synthetic and reproducible.
- The manual-triage baseline is a **documented per-step estimate**
  (`benchmark/baseline_methodology.md`), not a controlled study of human
  engineers.
- Commit relevance ranking is a transparent **heuristic** (time proximity plus
  path overlap), not a learned model.
- The Ops Agent falls back to a deterministic rule-based planner when
  `ANTHROPIC_API_KEY` is absent, so the graph and benchmark run offline. Which
  path executed is recorded in state and shown in the dashboard.
- Nothing is deployed to production. The dry-run gate marks a fix
  `ready_to_apply`; applying it is left to a human.
