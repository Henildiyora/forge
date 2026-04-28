from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge.cli.main import app


def test_setup_falls_back_to_heuristic_when_ollama_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "forge.cli.commands.setup._ollama_available", lambda _url: False
    )

    runner = CliRunner()
    result = runner.invoke(app, ["setup", str(tmp_path)])

    assert result.exit_code == 0
    profile_path = tmp_path / ".forge" / "connection.json"
    assert profile_path.exists()
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert payload["llm_backend"] == "heuristic"
    assert payload["llm_model"] == "heuristic-builtin"
    assert payload["approval_transport"] == "web"


def test_setup_picks_ollama_when_detected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "forge.cli.commands.setup._ollama_available", lambda _url: True
    )

    runner = CliRunner()
    result = runner.invoke(app, ["setup", str(tmp_path)])

    assert result.exit_code == 0
    payload = json.loads(
        (tmp_path / ".forge" / "connection.json").read_text(encoding="utf-8")
    )
    assert payload["llm_backend"] == "ollama"
    assert payload["llm_model"] == "qwen2.5-coder:1.5b"


def test_setup_respects_explicit_backend_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "forge.cli.commands.setup._ollama_available", lambda _url: True
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["setup", str(tmp_path), "--backend", "anthropic"],
    )

    assert result.exit_code == 0
    payload = json.loads(
        (tmp_path / ".forge" / "connection.json").read_text(encoding="utf-8")
    )
    assert payload["llm_backend"] == "anthropic"
    assert payload["llm_model"] == "claude-sonnet-4-20250514"


def test_doctor_quick_reports_python_and_heuristic_ok() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--quick"])

    assert result.exit_code == 0
    assert "Python >= 3.11" in result.stdout
    assert "Heuristic backend" in result.stdout
    assert "OK" in result.stdout


def test_reset_removes_dot_forge_directory(tmp_path: Path) -> None:
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "index.json").write_text("{}", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["reset", str(tmp_path), "--yes"])

    assert result.exit_code == 0
    assert not forge_dir.exists()


def test_reset_handles_missing_dot_forge(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["reset", str(tmp_path), "--yes"])

    assert result.exit_code == 0
    assert "nothing to do" in result.stdout
