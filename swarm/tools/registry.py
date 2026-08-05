"""The standardized tool-calling layer.

An agent never touches a client directly. It builds a :class:`ToolCall`, hands
it to the registry, and gets a :class:`ToolResult` back. The registry validates
arguments against the tool's declared Pydantic argument model and validates the
handler's return against its result model, so a malformed call fails at the
boundary instead of halfway through an agent.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from swarm.schemas import ToolCall, ToolResult

ArgsT = TypeVar("ArgsT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class ToolExecutionError(RuntimeError):
    """Raised when a tool call cannot be completed."""


@dataclass(frozen=True)
class Tool:
    """A callable exposed to agents under a stable name and schema."""

    name: str
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    handler: Callable[[Any], BaseModel]

    def json_schema(self) -> dict[str, Any]:
        """Schema description suitable for LLM tool-calling payloads."""

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
            "output_schema": self.result_model.model_json_schema(),
        }


class ToolRegistry:
    """Holds the tools available to a run and executes calls against them."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Add a tool, replacing any previous registration of the same name."""

        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Look up a registered tool."""

        if name not in self._tools:
            raise ToolExecutionError(f"unregistered tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        """Registered tool names, sorted."""

        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """JSON schemas for every registered tool."""

        return [self._tools[name].json_schema() for name in self.names()]

    def call(
        self,
        *,
        tool: str,
        agent: str,
        arguments: BaseModel | dict[str, Any],
    ) -> tuple[ToolCall, ToolResult]:
        """Build and execute a call in one step, returning both halves.

        Both halves are returned so the caller can record them on shared state,
        which gives the run a complete, replayable tool audit trail.
        """

        raw_args = (
            arguments.model_dump(mode="json")
            if isinstance(arguments, BaseModel)
            else dict(arguments)
        )
        call = ToolCall(tool=tool, agent=agent, arguments=raw_args)
        return call, self.invoke(call)

    def invoke(self, call: ToolCall) -> ToolResult:
        """Execute a previously built call."""

        started = time.perf_counter()

        def failure(message: str) -> ToolResult:
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                agent=call.agent,
                ok=False,
                error=message,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            tool = self.get(call.tool)
        except ToolExecutionError as exc:
            return failure(str(exc))

        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            return failure(f"invalid arguments for {call.tool}: {exc}")

        try:
            result = tool.handler(args)
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent as ok=False
            return failure(f"{type(exc).__name__}: {exc}")

        if not isinstance(result, tool.result_model):
            try:
                result = tool.result_model.model_validate(result)
            except ValidationError as exc:
                return failure(f"tool {call.tool} returned an invalid result: {exc}")

        return ToolResult(
            call_id=call.call_id,
            tool=call.tool,
            agent=call.agent,
            ok=True,
            payload=result.model_dump(mode="json"),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
