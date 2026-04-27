# Architecture

Sprint 1 establishes the shared primitives that every later DevOps Swarm
feature depends on:

- `Settings` centralizes configuration and safety toggles
- `SwarmEvent` defines all inter-agent contracts
- `MessageBus` enforces Redis Streams-based communication
- `BaseAgent` standardizes lifecycle, logging, and dead-letter handling
- `SwarmState` provides the shared orchestrator state shape
