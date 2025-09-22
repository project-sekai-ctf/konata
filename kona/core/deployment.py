import asyncio
from functools import cache
from pathlib import Path

import docker
from loguru import logger

from kona.schema.models import KonaChallengeConfig, KonaGlobalConfig


@cache
def docker_env() -> docker.DockerClient:
    return docker.from_env()


def _build_one(
    env: docker.DockerClient, context_dir: Path, full_ref: str, build_args: dict, platform: str | None
) -> None:
    _, logs = env.images.build(
        path=str(context_dir),
        tag=full_ref,
        nocache=True,
        pull=True,
        forcerm=True,
        buildargs=build_args,
        platform=platform,
    )
    for line in logs:
        logger.debug(str(line).strip())


def _push_one(env: docker.DockerClient, repository: str, tag: str) -> None:
    for line in env.images.push(
        repository=repository,
        tag=tag,
        stream=True,
        decode=True,
        auth_config={},  # https://github.com/docker/docker-py/issues/3348#issuecomment-3224418755
    ):
        logger.debug(str(line).strip())


async def build_docker_images(
    config: KonaGlobalConfig, path: Path, deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig
) -> None:
    if not deployment_config.images:
        # No need to call docker_env if we don't need it
        return

    env = docker_env()
    for image in deployment_config.images:
        repository = image.name
        if image.registry_name:
            registry = config.registries.get(image.registry_name)
            if not registry:
                msg = f'Unknown registry name "{image.registry_name}" for {image.name}:{image.tag}'
                raise ValueError(msg)

            repository = f'{registry.rstrip("/")}/{image.name}'

        full_ref = f'{repository}:{image.tag}'
        logger.info(f'Building {full_ref} for {image.name}:{image.tag}')

        await asyncio.to_thread(
            _build_one,
            env=env,
            context_dir=(path / image.path).resolve(),
            full_ref=full_ref,
            build_args=image.build_args,
            platform=image.platform,
        )

        if not image.registry_name:
            logger.warning(f'Skipping push for {full_ref} (no registry specified)')
            continue

        logger.info(f'Pushing {full_ref} for {image.name}:{image.tag}')
        await asyncio.to_thread(_push_one, env=env, repository=repository, tag=image.tag)


async def deploy_challenge(
    config: KonaGlobalConfig, path: Path, deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig
) -> None:
    await build_docker_images(config, path, deployment_config)
