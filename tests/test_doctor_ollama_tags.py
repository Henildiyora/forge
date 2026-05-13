from __future__ import annotations

from forge.cli.commands.doctor import _parse_ollama_tags_for_model


def test_parse_ollama_tags_model_present_exact() -> None:
    body = {"models": [{"name": "qwen2.5-coder:1.5b"}]}
    ok, msg = _parse_ollama_tags_for_model(body, "qwen2.5-coder:1.5b")
    assert ok is True
    assert msg == "qwen2.5-coder:1.5b"


def test_parse_ollama_tags_model_missing() -> None:
    body = {"models": [{"name": "llama3.2:latest"}]}
    ok, msg = _parse_ollama_tags_for_model(body, "qwen2.5-coder:1.5b")
    assert ok is False
    assert "ollama pull" in msg


def test_parse_ollama_tags_same_base_name() -> None:
    body = {"models": [{"name": "myapp:latest"}]}
    ok, msg = _parse_ollama_tags_for_model(body, "myapp")
    assert ok is True
    assert "myapp" in msg
