# Adding A New Integration

Integrations should inherit from `swarm.integrations.base.BaseIntegration`,
define explicit health checks, and keep secrets out of model-facing code paths.
