from __future__ import annotations

import asyncio

import structlog

from swarm.core.config import Settings
from swarm.core.logging import configure_logging


def bootstrap() -> Settings:
    settings = Settings()
    configure_logging(settings)
    structlog.get_logger().info(
        "application_bootstrapped",
        app_name=settings.app_name,
        environment=settings.app_env,
    )
    return settings


async def main() -> None:
    settings = bootstrap()
    structlog.get_logger().info(
        "foundation_ready",
        redis_url=settings.redis_url,
        dry_run_mode=settings.dry_run_mode,
    )


if __name__ == "__main__":
    asyncio.run(main())
