# FORGE Configuration Reference

FORGE works with zero environment variables. Defaults are tuned so that running
`forge index` and `forge build` in any project succeeds with no setup.

This page documents every setting that exists. Each one is optional. Set them
in `.env` at the project root or as real environment variables.

## Application

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_NAME` | `forge` | Logical app name in logs. |
| `APP_ENV` | `development` | One of `development`, `test`, `production`. |
| `LOG_LEVEL` | `INFO` | Standard log level. |
| `LOG_JSON` | `true` | Render logs as JSON when true. |

## LLM backend

FORGE supports five backends. The deterministic `heuristic` backend is always
available with no API key, no network, and no model download.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_BACKEND` | `heuristic` | One of `heuristic`, `ollama`, `llamacpp`, `anthropic`, `openai`. |
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Model name when using a remote backend. |
| `LLM_MAX_TOKENS` | `8192` | Maximum tokens per response. |
| `ANTHROPIC_API_KEY` | _(unset)_ | Required only when `LLM_BACKEND=anthropic`. |
| `OPENAI_API_KEY` | _(unset)_ | Required only when `LLM_BACKEND=openai`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama HTTP server. |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model tag. We recommend `qwen2.5-coder:1.5b` for laptops. |
| `LLAMACPP_BASE_URL` | `http://localhost:8080` | llama.cpp HTTP server URL. |
| `LLAMACPP_MODEL` | `local-gguf` | Reported model name for llama.cpp. |

## GitHub

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_TOKEN` | _(unset)_ | Optional. Enables Librarian commit context for incident triage. |
| `GITHUB_ORG` | _(unset)_ | Default org used by integrations. |

## Redis (message bus)

Redis is **optional**. When unreachable FORGE uses the in-process message bus
that ships in `forge/core/message_bus.py`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDIS_URL` | `redis://localhost:6379/0` | Connection URL when you opt in. |
| `REDIS_STREAM_PREFIX` | `forge` | Stream namespace. |
| `REDIS_STREAM_BLOCK_MS` | `1000` | Consumer block time. |
| `REDIS_CONSUMER_BATCH_SIZE` | `10` | Max events per poll. |
| `REDIS_STREAM_MAXLEN` | `10000` | Approximate max stream length. |

## Kubernetes

| Variable | Default | Purpose |
|----------|---------|---------|
| `KUBECONFIG_PATH` | `~/.kube/config` | kubectl config file. |
| `K8S_NAMESPACE` | `devops-forge` | Default namespace for cluster ops. |

## Observability

| Variable | Default | Purpose |
|----------|---------|---------|
| `PROMETHEUS_URL` | `http://localhost:9090` | Watchman queries this URL. |
| `LOKI_URL` | `http://localhost:3100` | Watchman queries this URL. |

## Slack approvals

| Variable | Default | Purpose |
|----------|---------|---------|
| `SLACK_WEBHOOK_URL` | _(unset)_ | Outgoing approval messages. |
| `SLACK_SIGNING_SECRET` | _(unset)_ | Verifies inbound interactive payloads. |
| `SLACK_APPROVAL_CHANNEL` | `#devops-approvals` | Channel for approval requests. |
| `APPROVAL_BASE_URL` | `http://localhost:8000` | Used in web fallback approval links. |

## Sandbox

| Variable | Default | Purpose |
|----------|---------|---------|
| `VCLUSTER_BINARY_PATH` | `/usr/local/bin/vcluster` | vcluster CLI binary location. |
| `SANDBOX_MAX_AGE_MINUTES` | `30` | Auto-destroy idle sandbox clusters after this long. |

## Safety switches

| Variable | Default | Purpose |
|----------|---------|---------|
| `DRY_RUN_MODE` | `true` | Global write switch. **Leave true** unless you know what you are doing. |
| `REQUIRE_HUMAN_APPROVAL` | `true` | Forces approval gate on every live change. |
