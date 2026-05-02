# FORGE Phase 2 — Surface and Capabilities

This page is the deep dive that the README intentionally skips. It documents
the Phase 2 conversation flow, supported strategies, and the safety
boundaries described in [`trust.md`](trust.md).

## Phase 2 surface

The CLI exposes workflow commands and utilities. The **Manager** path is the
default `forge build` experience: project preview, ranked strategies (top 3),
then specialist agents (Docker / Kubernetes / CI/CD) with **Captain** review for
Docker Compose, Kubernetes, and CI-only strategies; serverless and extend-existing
flows still use the legacy generator bundle.

| Command | Purpose |
|---------|---------|
| `forge index` | Run the Librarian scan and persist `.forge/index.json`. |
| `forge build` | Clarifications → preview → ranked strategies → Manager pipeline → artifacts. |
| `forge ask` / `forge chat` / `forge explain` | Talk to the Manager about the project or generated files. |
| `forge monitor` | Watchman snapshot or escalate to the incident workflow. |
| `forge connect` | Save project-local backend, model, approval transport, cloud preference. |
| `forge setup` | Pick the best LLM backend for the local machine. |
| `forge doctor` | Health-check the local environment. |
| `forge audit` | Show every action FORGE has taken in this project. |
| `forge reset` | Delete `.forge/`. |

## Manual quick start (alternative to `install.sh`)

```bash
python3 -m venv forge_venv
source forge_venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # optional
```

Optional local services:

```bash
docker compose -f docker-compose.dev.yml up -d
```

Run the FastAPI app for approvals:

```bash
uvicorn forge.api.app:create_app --factory --reload
```

## Build conversation, in detail

```bash
forge index /absolute/path/to/project
forge connect /absolute/path/to/project --backend heuristic --approval-transport web
forge build /absolute/path/to/project --goal "Deploy this API to Kubernetes" --auto-approve
```

`forge build`:

1. Loads or refreshes `.forge/index.json`.
2. Asks clarifying questions only when `intent` is missing required fields.
3. Prints a **project preview** (Manager) and optionally asks for confirmation.
4. Ranks the top strategies (`forge/conversation/strategy_ranking.py`) and shows pros/cons.
5. For **Docker Compose**, **Kubernetes**, and **CI/CD-only**, runs the Manager pipeline
   (specialists + `MessageBus` + Captain graph); otherwise uses the legacy generator bundle.
6. Writes artifacts under `.forge/generated/` (override with `--output-dir`).
7. When the strategy is Kubernetes, runs the SandboxTester against a vcluster.
8. With `--live`, requests an approval and pauses until granted.

### Docker intent vs Kubernetes complexity

For multi-service repositories, FORGE may detect Kubernetes pressure from the
scan (`service_count > 1`). If your goal text clearly asks for Docker
(`docker`, `dockerhub`, `containerize`), FORGE now asks you to choose
explicitly:

- **Docker Compose (simple, Docker-first)**
- **Kubernetes (scalable, cluster-first)**

Your answer is treated as an explicit strategy override.

### Kubernetes sandbox prerequisite

Kubernetes artifact validation requires `vcluster` locally. If `vcluster` is
missing, FORGE exits with a friendly actionable message instead of a Python
traceback, and suggests either:

1. install `vcluster` (`brew install loft-sh/tap/vcluster`), or
2. rerun and choose Docker Compose if your goal is Dockerfile/Docker Hub only.

## Monitor and incident

```bash
forge monitor payments \
  --incident \
  --error-rate 0.11 \
  --latency-p95-ms 900 \
  --restart-count 2 \
  --error-log-count 4
```

The remediation agent collects evidence, hypothesises a root cause (subject
to the hallucination guard), proposes a fix, runs an evaluation, and either
returns to observe or routes through approval + sandbox + live execution +
post-deploy rollback monitoring.

## Supported strategies

| Strategy | Outputs |
|----------|---------|
| `docker_compose` | Dockerfile + docker-compose YAML. |
| `kubernetes` | Dockerfile + Deployment/Service YAML + GitHub Actions. |
| `serverless` | AWS Lambda + API Gateway templates, or Google Cloud Run. |
| `cicd_only` | GitHub Actions YAML, no infra changes. |
| `extend_existing` | Additive overlay for repos with existing infra. |

## What works today

- `forge index` persists codebase scan context in `.forge/index.json`.
- `forge connect` stores backend, cloud, and approval preferences.
- `forge build` supports `docker_compose`, `kubernetes`, `serverless`,
  `extend_existing`, and `cicd_only` strategies.
- Serverless generation supports AWS Lambda + API Gateway and Google Cloud
  Run.
- Brownfield generation creates additive overlays instead of replacing
  existing infra.
- Approvals and workflow checkpoints survive across CLI/API handoffs through
  local persistence.
- Slack and web approval endpoints can resume waiting workflows.
- Incident remediation includes evidence collection, root-cause hypotheses,
  fix planning, reinvestigation loops, and approval checkpoints.
- Hardening, linting, typing, and tests are all runnable locally.

## Validation

```bash
make test
make lint
```

End-to-end:

```bash
make e2e
RUN_K8S_E2E=1 make e2e   # also runs cluster-only suites
```

Snapshot tests pin every generator's canonical output:

```bash
pytest tests/test_generator_snapshots.py
UPDATE_SNAPSHOTS=1 pytest tests/test_generator_snapshots.py   # re-record
```

## Current boundaries (v0.1)

- Live Kubernetes execution is safety-gated by the five checks documented
  in [`trust.md`](trust.md#the-five-gates-before-every-live-kubernetes-write).
- Cloud-provider mutations (AWS/GCP/Azure) are intentionally **not**
  shipped in v0.1; cloud read-only inspection is supported.
- Slack delivery requires you to provide credentials and a signing secret.
- The hallucination guard fails closed: low-confidence hypotheses route to
  observe-only mode, never to a fix proposal.
