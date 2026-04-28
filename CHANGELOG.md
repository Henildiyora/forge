# Changelog

All notable changes to FORGE.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

First public release. Phase 2 — trust, end-to-end, and simplicity.

### Added
- `install.sh` — one-line install via `curl ... | bash`, uses pipx.
- `forge setup` command — auto-detects Ollama, falls back to the offline
  heuristic backend so no API key is required.
- `forge doctor` command — Rich health table covering Python, Ollama,
  kubectl, Docker, vcluster, Redis, Slack signing secret.
- `forge audit` command — render and tail the project's append-only
  `.forge/audit.log`.
- `forge reset` command — delete `.forge/` for a clean slate.
- `forge/core/audit.py` — process-wide audit trail with structured
  `AuditEntry`s; wired into the kubectl client (`kubectl_apply`,
  `kubectl_rollback`, `live_gate_blocked`), Slack webhook
  (`approval_granted`, `approval_rejected`), and `forge build`
  (`artifact_written`).
- `MessageBus.in_memory()` and `InMemoryStreamClient` — Redis is now truly
  optional; the CLI works fully in-process.
- `tests/e2e/` directory with five suites:
  - `test_simple_project_e2e.py` — real `docker build` + container health
    check (gated by Docker availability).
  - `test_generation_path_e2e.py` — generator pipeline against every
    fixture (always runs).
  - `test_k8s_project_e2e.py` — real Kubernetes apply/rollout
    (gated by `RUN_K8S_E2E=1`).
  - `test_incident_e2e.py` — full evidence → hypothesis → fix → rollback
    loop, with optional cluster drill.
  - `test_rollback_drill.py` — bad manifest auto-rolled back within 60s
    (gated by `RUN_K8S_E2E=1`).
- `tests/test_generator_snapshots.py` — golden-file pinning for Dockerfile,
  K8s manifests, GitHub Actions output. Re-record with
  `UPDATE_SNAPSHOTS=1`.
- `tests/integration/test_live_gate.py` — covers all five
  `LiveExecutionGate` failure modes and the happy path.
- `tests/integration/test_slack_webhook_flow.py` — signed Slack payload
  smoke test that resumes a real workflow and audits the action.
- Hallucination guard at `assert_hypothesis_is_grounded`: every
  actionable fix proposal must cite evidence and clear the 0.70
  confidence threshold.
- `docs/trust.md`, `docs/configuration.md`, `docs/phase2.md`.
- `scripts/record-demo.sh` — drives a deterministic 30-second asciinema
  recording of the happy path.

### Changed
- README rewritten DocuMind-style: one-line install, three-command quick
  start, mermaid diagram, trust link. Sections that used to live in the
  README now live in `docs/phase2.md`.
- `.env.example` slimmed from 15+ vars to the 8 most useful, with full
  reference now in `docs/configuration.md`.
- `pyproject.toml`:
  - Renamed package distribution to `forge-devops` (PyPI-friendly).
  - Moved `ragas` to the new `forge-devops[observability]` extra.
  - Moved `redis` out of the required dependency list; available via
    `forge-devops[redis]` and bundled with `[dev]`.
  - Added rich PyPI metadata (classifiers, urls, license, keywords).
- `Makefile` paths now target `forge` everywhere; added `make e2e` and
  `make clean`.

### Removed
- The duplicate `swarm/` package tree. `forge/` is the single source of
  truth.
- `forge chaos` CLI subcommand (deferred from v0.1; the underlying
  hardening suite still lives in `forge/core/hardening.py`).

### Security / trust
- Every `kubectl apply` and `kubectl rollback` is now recorded in the
  audit log with task id and approver.
- Every `LiveExecutionGate` failure is logged as `live_gate_blocked` so
  refused operations are visible.
- Cloud writes (AWS/GCP) are intentionally not implemented in v0.1.
