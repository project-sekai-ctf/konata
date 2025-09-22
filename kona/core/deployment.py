import asyncio
from functools import cache
from pathlib import Path

import docker
import yaml
from kubernetes.client import ApiClient
from kubernetes.dynamic import DynamicClient
from loguru import logger

from kona.schema.models import KonaChallengeConfig, KonaGlobalConfig

from .kubernetes import load_kubeconfig


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


async def apply_kubernetes_manifests(
    config: KonaGlobalConfig, path: Path, deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig
) -> None:
    clusters_count = len(config.clusters)

    for manifest in deployment_config.kubernetes_manifests:
        if clusters_count != 1 and not manifest.cluster_name:
            msg = 'Cluster name should always be defined when there are more than one cluster defined'
            raise ValueError(msg)

        if manifest.cluster_name:
            load_kubeconfig(config, manifest.cluster_name)

        dyn = DynamicClient(ApiClient())

        manifest_data = (path / manifest.path).read_text()
        for document in yaml.safe_load_all(manifest_data):
            api_version = document['apiVersion']
            kind = document['kind']
            meta = document['metadata']
            name = meta['name']
            namespace = meta.get('namespace')

            resource = dyn.resources.get(api_version=api_version, kind=kind)

            kwargs = {}
            if resource.namespaced:
                kwargs['namespace'] = namespace or 'default'

            resource.patch(
                name=name,
                body=document,
                content_type='application/apply-patch+yaml',
                field_manager='kona',
                **kwargs,
            )
            logger.info(f'Applied {kind}/{name}')


async def deploy_challenge(
    config: KonaGlobalConfig, path: Path, deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig
) -> None:
    await build_docker_images(config, path, deployment_config)
    await apply_kubernetes_manifests(config, path, deployment_config)
