# DevOps Swarm

DevOps Swarm is a production-oriented multi-agent DevOps and SRE platform.
This repository currently implements all twelve planned sprints of the current foundation:
shared settings, structured logging, event contracts, a Redis Streams message
bus, deploy-planning specialists, orchestration, observability clients plus
Watchman anomaly detection, real Kubernetes operation wrappers, and sandbox
validation workflows, incident triage and approval-request handling, and
cloud-environment inventory plus deployment-target assessment, with a usable
operator CLI over the implemented workflows, plus evidence validation,
workflow observability summaries, and a built-in chaos hardening suite.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d
pytest -q
```

## Current Sprint Scope

- Core configuration via `swarm.core.config.Settings`
- Structured JSON logging via `swarm.core.logging`
- Event definitions in `swarm.core.events`
- Redis Streams wrapper in `swarm.core.message_bus`
- Base agent lifecycle in `swarm.agents.base`
- Librarian codebase scanning and diff classification
- Captain deploy-workflow orchestration and review
- Docker, Kubernetes, and CI/CD artifact generation
- Watchman Prometheus and Loki integrations with anomaly detection
- Kubernetes runtime operations with safe apply/read wrappers
- Sandbox cluster lifecycle and smoke-test validation
- Incident triage and approval-request generation
- Cloud inventory and deployment-target assessment
- Operational CLI commands for deploy, monitor, connect, init, and approvals
- Evidence-enforced LLM wrapper and workflow observability summaries
- Chaos hardening suite with fault injection and fail-safe reporting
- FastAPI API scaffolding

## Run What Exists

Use these commands from the repository root:

```bash
source .venv/bin/activate
```

Run the application bootstrap:

```bash
python -m swarm.main
```

Run the API server:

```bash
uvicorn swarm.api.app:create_app --factory --reload
```

Run the CLI status command:

```bash
python -m swarm.cli.main status
```

Run the deploy workflow locally:

```bash
python -m swarm.cli.main deploy ./path/to/project --output-dir ./artifacts
```

Run the Sprint 12 hardening suite:

```bash
python -m swarm.cli.main chaos ./path/to/project --json
```

Run incident-aware monitoring from a local snapshot:

```bash
python -m swarm.cli.main monitor payments --incident --error-rate 0.11 --latency-p95-ms 900 --restart-count 2 --error-log-count 4
```

Inspect cloud inventory from a local catalog:

```bash
python -m swarm.cli.main connect aws --catalog-file ./cloud_catalog.sample.json --account-id prod-123 --region us-east-1
```

Run the full validation suite:

```bash
pytest -q
ruff check .
mypy swarm tests
```

## What You Can Test Today

- Foundation bootstrapping and structured logging
- Redis Streams message-bus publish and consume behavior
- Librarian project scanning for Python, Node, and Go samples
- Captain deploy review and retry logic
- Deploy workflow end-to-end artifact generation
- Dockerfile, Kubernetes manifest, and CI/CD pipeline generation
- Watchman Prometheus and Loki query wrappers
- Watchman anomaly detection from mocked observability data
- Kubernetes pod inspection, namespace event lookup, and manifest validation
- Sandbox lifecycle orchestration and smoke-test validation from mocked runtimes
- Incident workflow routing and approval API state transitions
- Cloud inventory and deployment-readiness assessment from mocked MCP catalogs
- CLI-driven deploy, monitor, connect, init, and approval flows
- Evidence guard validation and observability API summaries
- Chaos hardening scenarios for specialist failure, retry exhaustion, approval gating, and LLM evidence blocking
- FastAPI health endpoints and API smoke tests

## Not Yet Runnable End-to-End

- Live deployment to Kubernetes clusters
- Automated incident remediation execution
- Live cloud-provider mutation flows
- Full monitor CLI workflow
- External approval workflow integrations such as Slack

## Repository Layout

The repository follows the master DevOps Swarm blueprint so future sprints can
fill in agent specialization, orchestration, approvals, and sandbox workflows
without structural churn.
