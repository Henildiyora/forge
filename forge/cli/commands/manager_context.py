"""Load workspace context for Manager (`forge ask`, `forge chat`, `forge explain`)."""

from __future__ import annotations

import json
from pathlib import Path


def load_manager_context(project_root: Path) -> str:
    """Summarize index, session, and generated manifest for the Manager."""

    parts: list[str] = []
    forge_dir = project_root / ".forge"
    index_path = forge_dir / "index.json"
    if index_path.is_file():
        parts.append("=== .forge/index.json (summary) ===")
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            parts.append(
                f"language={data.get('language')} framework={data.get('framework')} "
                f"port={data.get('port')} services={data.get('service_count')}"
            )
        except (json.JSONDecodeError, OSError) as exc:
            parts.append(f"(could not read index: {exc})")
    session_path = forge_dir / "session.json"
    if session_path.is_file():
        parts.append("\n=== .forge/session.json ===")
        try:
            sess = json.loads(session_path.read_text(encoding="utf-8"))
            parts.append(json.dumps(sess, indent=2)[:4000])
        except (json.JSONDecodeError, OSError) as exc:
            parts.append(f"(could not read session: {exc})")
    manifest_path = forge_dir / "artifacts.json"
    if manifest_path.is_file():
        parts.append("\n=== .forge/artifacts.json ===")
        try:
            parts.append(manifest_path.read_text(encoding="utf-8")[:2000])
        except OSError as exc:
            parts.append(f"(could not read manifest: {exc})")
    if not parts:
        return "No .forge workspace found. Run `forge index` and `forge build` in this project first."
    return "\n".join(parts)
