# Trust & Safety

FORGE writes to your filesystem, your container runtime, your Kubernetes
cluster, and your incident channels. This page is the explicit contract
between you and FORGE about what it will do, what it refuses to do, and
how to verify everything after the fact.

If you only read one section, read [What FORGE will not touch](#what-forge-will-not-touch).

## TL;DR

- FORGE never writes to a live Kubernetes cluster without (a) a passing
  sandbox validation, (b) a passing dry-run, (c) a human approval, and
  (d) a non-null `task_id` for the audit trail.
- FORGE never sends data to a cloud LLM unless you opted in. The default
  backend is the in-process heuristic engine; it has zero network egress.
- FORGE never modifies files outside `<project>/.forge/` and `<output_dir>`
  (the directory you pointed `forge build` at).
- Every action that touches a real system is appended to
  `<project>/.forge/audit.log` as JSON Lines. Run `forge audit` to read it.

## What FORGE will not touch

| System | Default behaviour | Override (`v0.1`) |
|--------|-------------------|-------------------|
| Live Kubernetes cluster | Refuses unless five gates pass | Manual `--live` + approval |
| AWS / GCP / Azure (writes) | **Not implemented in v0.1** | _(deferred)_ |
| AWS / GCP / Azure (reads) | Optional, only when you set creds | Configure provider |
| Slack | Refuses unless `SLACK_SIGNING_SECRET` set | Per-action env opt-in |
| GitHub repositories | Read-only via personal token | _(no write features yet)_ |
| Files outside `.forge/` | Generates only into `output_dir` | `forge build --output-dir` |
| Production secrets | Never read or stored by FORGE | Use Vault/KMS, FORGE reads env only |

In v0.1, **cloud writes are deliberately not shipped**. Cloud read-only
inspection (cost, posture, environment metadata) is available; everything
mutating goes through Kubernetes + Slack + audit log only.

## The five gates before every live Kubernetes write

`forge/agents/k8s_specialist/kubectl_client.py::live_execution_gate`
enforces every one of the following. If any returns `False`, the apply
raises `ConfigurationError` and never reaches your cluster:

1. **`dry_run_mode is False`** — global safety toggle, default `True`.
2. **`sandbox_test_passed is True`** — the same manifests must have first
   succeeded against an ephemeral sandbox cluster (`vcluster`/`kind`).
3. **`approval_status == "approved"`** — a human (Slack or web) granted
   the request through the approval store.
4. **`task_id` is set** — required so every action is correlatable in
   logs and the audit file.
5. **`dry_run_passed is True`** — the same manifests must have first
   succeeded a server-side `kubectl apply --dry-run=server`.

A blocked attempt is logged with `action=live_gate_blocked` plus the
exact reason in the audit file.

## Hallucination guard

LLM-produced root-cause diagnoses must clear two checks before FORGE
will turn them into a fix proposal that changes anything (rollback,
config change, restart):

- **Evidence non-empty** — at least one `EvidenceItem` must back the
  hypothesis.
- **Confidence ≥ 0.70** — the threshold defined by
  `MIN_HYPOTHESIS_CONFIDENCE` in `forge/agents/remediation/fix_evaluator.py`.

If either fails, `assert_hypothesis_is_grounded` raises
`InsufficientEvidenceError`. Low-confidence signals are downgraded to
the `observe` strategy, which takes no action.

## Approval workflow

Live changes always require an approval, regardless of how confident the
agents are. Approvals can be granted through:

- The web fallback at `<APPROVAL_BASE_URL>/api/v1/approvals/<task-id>`.
- Slack interactive buttons. The signature is verified against
  `SLACK_SIGNING_SECRET`; bad signatures get an HTTP 403.

Both paths route through `forge/api/routers/slack_webhooks.py` and
`forge/core/resume.py`. Every approve/reject is recorded in the audit
log with the requesting user and action ID.

## Audit log

`<project>/.forge/audit.log` is append-only JSON Lines. Each entry has:

```json
{"timestamp":"...","actor":"...","action":"kubectl_apply","target":"namespace=demo","task_id":"...","approval_id":"...","evidence":[...],"detail":{...}}
```

Actions FORGE records:

- `kubectl_apply`, `kubectl_rollback`, `kubectl_delete`
- `slack_send`, `slack_action_received`
- `approval_granted`, `approval_rejected`
- `cloud_api_call` (read-only in v0.1)
- `fix_applied`, `rollback_triggered`
- `live_gate_blocked` (when a gate refused an action)
- `artifact_written`

Read it with: `forge audit` (Rich table) or `forge audit --raw`
(JSON Lines, perfect for `jq` or shipping to a SIEM).

The log is never edited or rewritten by FORGE. If you see a gap, FORGE
truly did not record an action there — not that the action was hidden.

## Data locality

- All FORGE state lives at `<project>/.forge/`. Delete with `forge reset`.
- The default LLM backend (`heuristic`) makes no network calls.
- Ollama and llama.cpp keep prompts on `localhost`.
- Anthropic and OpenAI backends only run if you set the respective env
  vars; FORGE never inspects them otherwise.
- GitHub access is read-only and uses your supplied PAT.

## How to revoke FORGE

1. `forge reset` removes `<project>/.forge/`.
2. Revoke any tokens you set (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`,
   `SLACK_SIGNING_SECRET`).
3. `pipx uninstall forge` or remove the FORGE installation directory.
4. Cluster artefacts that FORGE applied are listed in the audit log;
   delete them by namespace or with `kubectl delete -f <output_dir>`.

## Reporting issues

If you find a way to bypass any of the gates above, please open an issue
tagged `security` or email the maintainers privately first.
