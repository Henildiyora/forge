from __future__ import annotations

from pydantic import BaseModel, Field


class ClarificationOption(BaseModel):
    """Single selectable clarification option shown in the FORGE terminal UI."""

    key: str = Field(description="Stable option identifier.")
    label: str = Field(description="Human-readable option label.")
    value: str = Field(description="Structured value recorded when selected.")


class ClarificationQuestion(BaseModel):
    """Clarification question displayed when FORGE lacks enough confidence."""

    question_key: str = Field(description="Stable question identifier.")
    prompt: str = Field(description="Question shown to the user.")
    options: list[ClarificationOption] = Field(
        default_factory=list,
        description="Available answer options, maximum four.",
    )
    rationale: str = Field(
        description="Why FORGE needs this question before recommending a strategy.",
    )

    def render_terminal_box(self) -> str:
        """Render the prompt in the structured terminal format required by Phase 2."""

        width = 79
        lines = [
            "FORGE needs one more thing to recommend the",
            "right deployment strategy:",
            "",
            self.prompt,
            "",
        ]
        for index, option in enumerate(self.options, start=1):
            lines.append(f"  [{index}] {option.label}")
        lines.extend(["", "Type a number or describe in your own words:"])
        bordered = ["┌" + "─" * width + "┐"]
        for line in lines:
            bordered.append(f"│ {line.ljust(width - 1)}│")
        bordered.append("└" + "─" * width + "┘")
        return "\n".join(bordered)
