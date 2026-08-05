"""Dry-run helpers that do not require Docker."""

from __future__ import annotations

from swarm.dryrun.docker_sandbox import apply_actions_to_env
from swarm.schemas import FixAction, FixActionKind


def test_apply_env_overrides():
    env = {"PAYMENT_TIMEOUT_MS": "50", "MAX_RETRIES": "3"}
    updated = apply_actions_to_env(
        [
            FixAction(
                kind=FixActionKind.ENV_OVERRIDE,
                target="PAYMENT_TIMEOUT_MS",
                value="2000",
            )
        ],
        env,
    )
    assert updated["PAYMENT_TIMEOUT_MS"] == "2000"
    assert updated["MAX_RETRIES"] == "3"
