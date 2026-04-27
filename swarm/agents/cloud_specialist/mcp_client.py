from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

CloudProvider = Literal["aws", "gcp", "azure"]


class MCPResource(BaseModel):
    """Normalized cloud resource returned through the MCP integration layer."""

    provider: CloudProvider = Field(description="Cloud provider owning the resource.")
    service: str = Field(description="Provider service such as eks, ecr, rds, or gke.")
    resource_id: str = Field(description="Stable provider-side resource identifier.")
    name: str = Field(description="Human-readable resource name.")
    region: str = Field(description="Cloud region where the resource lives.")
    account_id: str = Field(description="Owning account or project identifier.")
    status: str = Field(default="unknown", description="Normalized health status.")
    public_exposure: bool = Field(
        default=False,
        description="Whether the resource is directly internet-exposed.",
    )
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Provider tags or labels normalized to strings.",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Additional provider-specific details preserved for assessments.",
    )


class CloudEnvironmentSummary(BaseModel):
    """Aggregated snapshot of a provider environment."""

    provider: CloudProvider
    resource_count: int = Field(ge=0)
    resources_by_service: dict[str, int] = Field(default_factory=dict)
    resources_by_region: dict[str, int] = Field(default_factory=dict)
    unhealthy_resources: list[str] = Field(default_factory=list)
    public_resources: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class CloudDeploymentAssessment(BaseModel):
    """Assessment of whether a provider environment fits a deployment or incident need."""

    provider: CloudProvider
    target_service: str = Field(description="Logical service under assessment.")
    readiness_score: float = Field(ge=0.0, le=1.0)
    blockers: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    matched_resources: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class MCPClient:
    """Read-only MCP-style client used for cloud inventory and assessment."""

    def __init__(
        self,
        *,
        resource_catalog: Mapping[str, list[dict[str, object]]] | None = None,
    ) -> None:
        catalog = resource_catalog or {}
        self._catalog: dict[str, list[MCPResource]] = {
            provider: [MCPResource.model_validate(resource) for resource in resources]
            for provider, resources in catalog.items()
        }

    async def list_resources(
        self,
        provider: CloudProvider,
        *,
        account_id: str | None = None,
        region: str | None = None,
    ) -> list[MCPResource]:
        resources = list(self._catalog.get(provider, []))
        if account_id is not None:
            resources = [resource for resource in resources if resource.account_id == account_id]
        if region is not None:
            resources = [resource for resource in resources if resource.region == region]
        return [resource.model_copy(deep=True) for resource in resources]

    async def summarize_environment(
        self,
        provider: CloudProvider,
        *,
        account_id: str | None = None,
        region: str | None = None,
    ) -> CloudEnvironmentSummary:
        resources = await self.list_resources(provider, account_id=account_id, region=region)
        services = Counter(resource.service for resource in resources)
        regions = Counter(resource.region for resource in resources)
        unhealthy_resources = [
            resource.resource_id
            for resource in resources
            if resource.status.lower() not in {"running", "available", "healthy", "active"}
        ]
        public_resources = [
            resource.resource_id for resource in resources if resource.public_exposure is True
        ]
        evidence = [
            f"Discovered {len(resources)} resource(s) for provider {provider}.",
            f"Services present: {', '.join(sorted(services)) or 'none'}.",
            f"Regions present: {', '.join(sorted(regions)) or 'none'}.",
        ]
        if unhealthy_resources:
            evidence.append(
                f"Found unhealthy resources: {', '.join(unhealthy_resources)}."
            )
        if public_resources:
            evidence.append(f"Found public resources: {', '.join(public_resources)}.")
        confidence = 0.93 if resources else 0.72
        return CloudEnvironmentSummary(
            provider=provider,
            resource_count=len(resources),
            resources_by_service=dict(services),
            resources_by_region=dict(regions),
            unhealthy_resources=unhealthy_resources,
            public_resources=public_resources,
            evidence=evidence,
            confidence=confidence,
        )

    async def assess_deployment_target(
        self,
        provider: CloudProvider,
        *,
        target_service: str,
        account_id: str | None = None,
        region: str | None = None,
        deployment_context: dict[str, object] | None = None,
    ) -> CloudDeploymentAssessment:
        resources = await self.list_resources(provider, account_id=account_id, region=region)
        context = deployment_context or {}
        blockers: list[str] = []
        recommendations: list[str] = []
        evidence: list[str] = []

        matched_resources = [
            resource.resource_id
            for resource in resources
            if target_service.lower() in resource.name.lower()
            or resource.tags.get("service", "").lower() == target_service.lower()
        ]
        if matched_resources:
            evidence.append(
                f"Matched resources for {target_service}: {', '.join(matched_resources)}."
            )
        else:
            evidence.append(f"No direct resources matched target service {target_service}.")

        needs_kubernetes = bool(context.get("needs_kubernetes", True))
        needs_registry = bool(context.get("needs_registry", True))
        needs_secrets = bool(context.get("needs_secrets_manager", True))

        if needs_kubernetes and not any(
            resource.service in {"eks", "gke", "aks", "kubernetes"}
            for resource in resources
        ):
            blockers.append(
                "No Kubernetes control plane resource was found for the target environment."
            )
        if needs_registry and not any(
            resource.service in {"ecr", "artifact-registry", "acr", "container-registry"}
            for resource in resources
        ):
            recommendations.append(
                "Add or verify a container registry before attempting image-based deployments."
            )
        if needs_secrets and not any(
            resource.service in {"secrets-manager", "secret-manager", "key-vault"}
            for resource in resources
        ):
            recommendations.append(
                "Provision a managed secrets backend so application credentials "
                "stay out of manifests."
            )

        for resource in resources:
            if (
                resource.public_exposure
                and resource.service in {"rds", "cloud-sql", "sql-database", "redis", "memorystore"}
            ):
                blockers.append(
                    f"Resource {resource.resource_id} is publicly exposed and should be isolated."
                )
            if resource.status.lower() not in {"running", "available", "healthy", "active"}:
                recommendations.append(
                    f"Investigate resource {resource.resource_id} because it is in status "
                    f"{resource.status}."
                )

        if any(resource.region != region for resource in resources) and region is not None:
            recommendations.append(
                f"Verify cross-region dependencies because not all resources are in {region}."
            )
        readiness_score = 0.92
        readiness_score -= 0.25 * len(blockers)
        readiness_score -= 0.05 * len(recommendations)
        readiness_score = max(0.05, min(readiness_score, 0.98))
        evidence.append(
            f"Computed readiness score {readiness_score:.2f} from {len(blockers)} blocker(s) "
            f"and {len(recommendations)} recommendation(s)."
        )
        confidence = 0.9 if resources else 0.68
        return CloudDeploymentAssessment(
            provider=provider,
            target_service=target_service,
            readiness_score=readiness_score,
            blockers=blockers,
            recommendations=recommendations,
            matched_resources=matched_resources,
            evidence=evidence,
            confidence=confidence,
        )
