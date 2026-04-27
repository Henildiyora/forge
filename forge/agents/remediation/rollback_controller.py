from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field


class RollbackResult(BaseModel):
    """Outcome of a post-deployment watch window."""

    rolled_back: bool = Field(description="Whether a rollback was triggered.")
    reason: str = Field(description="Reason for success or rollback.")
    observed_error_rates: list[float] = Field(
        default_factory=list,
        description="Observed error-rate samples gathered during the watch window.",
    )


@dataclass(frozen=True)
class RollbackController:
    """Observe a live deployment and rollback automatically when regressions appear."""

    metrics_reader: Callable[[str, str], Awaitable[float]]
    rollback_executor: Callable[[str, str, str], Awaitable[None]]
    rollback_threshold_error_rate: float = 0.05
    observation_window_seconds: int = 60
    poll_interval_seconds: int = 5

    async def watch_and_rollback_if_needed(
        self,
        *,
        namespace: str,
        deployment_name: str,
        previous_revision: str,
        task_id: str,
    ) -> RollbackResult:
        samples: list[float] = []
        del task_id
        poll_count = max(1, self.observation_window_seconds // self.poll_interval_seconds)
        for _ in range(poll_count):
            error_rate = await self.metrics_reader(namespace, deployment_name)
            samples.append(error_rate)
            if error_rate > self.rollback_threshold_error_rate:
                await self.rollback_executor(namespace, deployment_name, previous_revision)
                return RollbackResult(
                    rolled_back=True,
                    reason=(
                        f"Error rate {error_rate:.3f} exceeded rollback threshold "
                        f"{self.rollback_threshold_error_rate:.3f}."
                    ),
                    observed_error_rates=samples,
                )
        return RollbackResult(
            rolled_back=False,
            reason="Deployment stayed healthy for the full observation window.",
            observed_error_rates=samples,
        )
