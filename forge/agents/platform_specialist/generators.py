from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from forge.agents.librarian.ast_analyzer import CodebaseScanResult


class ExistingPlatformBundle(BaseModel):
    """Overlay artifacts used when extending an existing deployment setup."""

    files: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


def generate_existing_platform_overlay(scan_result: CodebaseScanResult) -> ExistingPlatformBundle:
    """Generate overlay manifests and notes for brownfield deployment environments."""

    app_name = Path(scan_result.project_path).name.strip().lower().replace("_", "-") or "app"
    overlay = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": app_name},
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "forge.dev/strategy": "extend_existing",
                        "forge.dev/managed": "true",
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": app_name,
                            "env": [
                                {"name": env_var, "value": f"set-{env_var.lower()}"}
                                for env_var in scan_result.env_vars
                            ],
                        }
                    ]
                },
            }
        },
    }
    notes = "\n".join(
        [
            "# FORGE overlay notes",
            f"- Existing infra detected: {', '.join(scan_result.detected_infra) or 'unknown'}",
            "- Review namespaces, registry coordinates, and secrets before applying.",
        ]
    )
    return ExistingPlatformBundle(
        files={
            "overlays/forge-overlay.yaml": yaml.safe_dump(overlay, sort_keys=False),
            "overlays/README.md": notes,
        },
        evidence=[
            "Detected existing infrastructure in the repository.",
            "Generated an additive overlay instead of a full replacement manifest set.",
        ],
        confidence=0.8,
    )
