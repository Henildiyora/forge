# FORGE

FORGE is a terminal-first AI DevOps and SRE platform. It scans application
codebases, stores project context in `.forge/`, runs a structured build
conversation, picks a deterministic deployment strategy, generates deployment
artifacts, and routes approvals and incident remediation through checkpointed
workflows.

## Phase 2 Surface

- `forge connect`
- `forge index`
- `forge build`
- `forge monitor`

## Quick Start

```bash
python3 -m venv forge_venv
source forge_venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Optional local services:

```bash
docker compose -f docker-compose.dev.yml up -d
```

## Main Commands

Index a project and persist `.forge/index.json`:

```bash
forge index /absolute/path/to/project
```

Save project-local backend and approval preferences:

```bash
forge connect /absolute/path/to/project --backend heuristic --approval-transport web
```

Run the build conversation and generate artifacts:

```bash
forge build /absolute/path/to/project --goal "Deploy this API to Kubernetes" --auto-approve
```

Escalate a monitoring snapshot into the incident workflow:

```bash
forge monitor payments --incident --error-rate 0.11 --latency-p95-ms 900 --restart-count 2 --error-log-count 4
```

Run the API:

```bash
uvicorn forge.api.app:create_app --factory --reload
```

## What Works Today

- `forge index` persists codebase scan context in `.forge/index.json`
- `forge connect` stores backend, cloud, and approval preferences
- `forge build` supports `docker_compose`, `kubernetes`, `serverless`,
  `extend_existing`, and `cicd_only` strategies
- serverless generation supports AWS Lambda + API Gateway and Google Cloud Run
- brownfield generation creates additive overlays instead of replacing existing infra
- approvals and workflow checkpoints survive across CLI/API handoffs through local persistence
- Slack and web approval endpoints can resume waiting workflows
- incident remediation now includes evidence collection, root-cause hypotheses,
  fix planning, reinvestigation loops, and approval checkpoints
- hardening, linting, typing, and tests are all runnable locally

## Validation

```bash
pytest -q
ruff check forge tests
mypy forge tests
```

## Current Boundaries

- live Kubernetes execution is still safety-gated and conservative
- automatic cloud-provider mutations outside Kubernetes are not implemented
- Slack delivery helpers exist, but full external channel delivery depends on your credentials and runtime setup
