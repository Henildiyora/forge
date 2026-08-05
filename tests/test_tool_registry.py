"""Standardized tool-calling envelope."""

from __future__ import annotations

from pydantic import BaseModel, Field

from swarm.tools.registry import Tool, ToolRegistry


class AddArgs(BaseModel):
    a: int
    b: int = 1


class AddResult(BaseModel):
    total: int = Field(ge=0)


def test_registry_validates_args_and_results():
    registry = ToolRegistry()

    def handler(args: AddArgs) -> AddResult:
        return AddResult(total=args.a + args.b)

    registry.register(
        Tool(
            name="math.add",
            description="add",
            args_model=AddArgs,
            result_model=AddResult,
            handler=handler,
        )
    )
    call, result = registry.call(tool="math.add", agent="test", arguments={"a": 2, "b": 3})
    assert call.tool == "math.add"
    assert result.ok is True
    assert result.payload["total"] == 5


def test_registry_surfaces_invalid_args():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="math.add",
            description="add",
            args_model=AddArgs,
            result_model=AddResult,
            handler=lambda args: AddResult(total=args.a + args.b),
        )
    )
    _, result = registry.call(tool="math.add", agent="test", arguments={"a": "nope"})
    assert result.ok is False
    assert "invalid arguments" in (result.error or "")
