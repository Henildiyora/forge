# Runbook

## Health

- `make test` validates the local foundation.
- `curl http://localhost:8000/health` confirms the API process is healthy.
- `redis-cli XLEN swarm:dlq` shows dead-letter queue depth once Redis is running.

## Current Status

This runbook currently documents Sprint 1 behavior only. Workflow replay,
incident handling, and sandbox operations will be added in later sprints.
