"""Tests for LiveConfig mapping and Ops allowlist filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm.fix_filter import apply_fix_allowlist
from swarm.live import LiveConfig, LiveConfigError, load_live_config, save_live_config
from swarm.schemas import FixAction, FixActionKind, ProposedFix, RiskLevel


def test_live_config_maps_to_swarm_config(tmp_path: Path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    cfg = LiveConfig(
        prometheus_url="http://localhost:9090",
        error_metric_query='rate(http_requests_total{code=~"5.."}[5m])',
        github_repo="acme/checkout",
        service_name="checkout",
        service_paths=["src/"],
        service_health_endpoints=["/health"],
        service_dockerfile_path=str(dockerfile),
        service_build_context=str(tmp_path),
        fix_action_allowlist=["env_override"],
    )
    swarm = cfg.to_swarm_config(max_repair_attempts=0)
    assert swarm.commit_source == "live"
    assert swarm.metric_query.startswith("rate(")
    assert swarm.repository == "acme/checkout"
    assert swarm.health_endpoints == ["/health"]
    assert swarm.service_root == str(tmp_path.resolve())
    assert swarm.fix_action_allowlist == ["env_override"]


def test_save_and_load_roundtrip(tmp_path: Path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    cfg = LiveConfig(
        prometheus_url="http://127.0.0.1:9090",
        error_metric_query="up",
        github_repo="acme/svc",
        service_health_endpoints=["/healthz"],
        service_dockerfile_path=str(dockerfile),
        service_build_context=str(tmp_path),
    )
    path = tmp_path / "config.yaml"
    save_live_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    # Comment may mention GITHUB_TOKEN; the YAML body must not store a secret value.
    assert "github_token:" not in text
    assert "ghp_" not in text
    assert "github_repo: acme/svc" in text
    loaded = load_live_config(path)
    assert loaded.github_repo == "acme/svc"
    assert loaded.prometheus_url == "http://127.0.0.1:9090"


def test_load_missing_file(tmp_path: Path):
    with pytest.raises(LiveConfigError, match="No live config"):
        load_live_config(tmp_path / "missing.yaml")


def test_allowlist_filters_actions():
    fix = ProposedFix(
        summary="test",
        root_cause="x",
        actions=[
            FixAction(kind=FixActionKind.ENV_OVERRIDE, target="A", value="1"),
            FixAction(kind=FixActionKind.REVERT_COMMIT, target="abc", value=""),
        ],
        risk_level=RiskLevel.LOW,
        confidence=0.5,
        source="heuristic",
    )
    filtered = apply_fix_allowlist(fix, ["env_override"])
    assert len(filtered.actions) == 1
    assert filtered.actions[0].kind == FixActionKind.ENV_OVERRIDE
    untouched = apply_fix_allowlist(fix, None)
    assert len(untouched.actions) == 2
