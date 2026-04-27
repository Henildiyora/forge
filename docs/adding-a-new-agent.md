# Adding A New Agent

1. Create the agent package under `swarm/agents/`.
2. Inherit from `swarm.agents.base.BaseAgent`.
3. Add any new event types to `swarm.core.events`.
4. Register the agent in `swarm.core.registry.AgentRegistry`.
5. Add tests under the agent package.
6. Update architecture docs if the agent changes public behavior.
