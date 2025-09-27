import asyncio
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from functools import cache
from http import HTTPStatus
from pathlib import Path
from typing import Any

import docker
import yaml
from kubernetes.client import ApiClient, ApiException
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import ResourceNotFoundError
from loguru import logger

from kona.schema.models import KonaChallengeConfig, KonaGlobalConfig

from .kubernetes import load_kubeconfig


DEFAULT_NAMESPACE = 'default'


@dataclass
class DeploymentResult:
    deployed_kubernetes_manifests: list[dict] = field(default_factory=list)
    built_docker_images: list[str] = field(default_factory=list)


@cache
def docker_env() -> docker.DockerClient:
    return docker.from_env()


def docker_build_image(
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


def docker_push_image(env: docker.DockerClient, repository: str, tag: str) -> None:
    for line in env.images.push(
        repository=repository,
        tag=tag,
        stream=True,
        decode=True,
        auth_config={},  # https://github.com/docker/docker-py/issues/3348#issuecomment-3224418755
    ):
        logger.debug(str(line).strip())


async def docker_build_images(
    result: DeploymentResult,
    config: KonaGlobalConfig,
    path: Path,
    deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig,
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
            docker_build_image,
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
        await asyncio.to_thread(docker_push_image, env=env, repository=repository, tag=image.tag)
        result.built_docker_images.append(full_ref)


def _to_dict(obj: Any) -> dict[str, Any]:  # noqa: ANN401
    return obj.to_dict() if hasattr(obj, 'to_dict') else obj


def k8s_is_crd(doc: dict[str, Any]) -> bool:
    return doc.get('apiVersion') == 'apiextensions.k8s.io/v1' and doc.get('kind') == 'CustomResourceDefinition'


async def k8s_wait_absent(
    resource: Any,  # noqa: ANN401
    name: str,
    kwargs: dict[str, Any],
    timeout: float = 120.0,  # noqa: ASYNC109
    interval: float = 1.0,
) -> None:
    async with asyncio.timeout(timeout):
        while True:
            try:
                resource.get(name=name, **kwargs)
            except ApiException as e:
                if e.status == HTTPStatus.NOT_FOUND:
                    return
            await asyncio.sleep(interval)


def k8s_get_resource(dyn: DynamicClient, api_version: str, kind: str) -> Any:  # noqa: ANN401
    try:
        return dyn.resources.get(api_version=api_version, kind=kind)
    except ResourceNotFoundError as err:
        msg = f'Unknown resource {api_version}/{kind}. Is the CRD installed?'
        raise ValueError(msg) from err


def k8s_build_kwargs(resource: Any, meta: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN401
    kwargs: dict[str, Any] = {}
    if getattr(resource, 'namespaced', False):
        kwargs['namespace'] = meta.get('namespace') or DEFAULT_NAMESPACE
    return kwargs


def k8s_resource_exists(resource: Any, name: str, kwargs: dict[str, Any]) -> bool:  # noqa: ANN401
    try:
        resource.get(name=name, **kwargs)
    except ApiException as e:
        if e.status == HTTPStatus.NOT_FOUND:
            return False
        raise
    else:
        return True


def k8s_try_patch_manifest(
    resource: Any,  # noqa: ANN401
    name: str,
    document: dict[str, Any],
    field_manager: str,
    kwargs: dict[str, Any],
    pre_rv: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    try:
        patched = resource.patch(
            name=name,
            body=document,
            content_type='application/apply-patch+yaml',
            field_manager=field_manager,
            **kwargs,
        )
        patched_dict = _to_dict(patched)
    except ApiException as e:
        if e.status == HTTPStatus.CONFLICT:
            try:
                forced = resource.patch(
                    name=name,
                    body=document,
                    content_type='application/apply-patch+yaml',
                    field_manager=field_manager,
                    force=True,
                    **kwargs,
                )
                return 'updated(force)', _to_dict(forced)
            except ApiException:
                return None
        return None
    else:
        if pre_rv is not None and (patched_dict.get('metadata') or {}).get('resourceVersion') == pre_rv:
            return 'unchanged', patched_dict
        return 'updated', patched_dict


async def k8s_recreate_manifest(
    resource: Any,  # noqa: ANN401
    name: str,
    document: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    try:
        resource.delete(
            name=name,
            propagation_policy='Foreground',
            grace_period_seconds=0,
            **kwargs,
        )
    except ApiException as del_e:
        if del_e.status != HTTPStatus.NOT_FOUND:
            raise
    await k8s_wait_absent(resource, name, kwargs)
    created = resource.create(body=document, **kwargs)
    return _to_dict(created)


async def k8s_wait_crd_established(
    dyn: DynamicClient,
    name: str,
    timeout: float = 120.0,  # noqa: ASYNC109
) -> None:
    logger.info(f'Waiting for CRD to become available for {name}')
    crd_res = dyn.resources.get(api_version='apiextensions.k8s.io/v1', kind='CustomResourceDefinition')
    async with asyncio.timeout(timeout):
        while True:
            obj = crd_res.get(name=name)
            d = obj.to_dict()
            conds = {c.get('type'): c.get('status') for c in (d.get('status', {}).get('conditions') or [])}
            if conds.get('Established') == 'True' and conds.get('NamesAccepted', 'True') != 'False':
                return
            await asyncio.sleep(0.5)


def k8s_refresh_discovery(dyn: DynamicClient) -> None:
    with suppress(BaseException):
        dyn.resources.invalidate_cache()


async def k8s_upsert_manifest(
    dyn: DynamicClient,
    document: dict[str, Any],
    field_manager: str = 'kona',
) -> tuple[str, dict[str, Any]]:
    api_version = document.get('apiVersion')
    kind = document.get('kind')
    meta = document.get('metadata') or {}
    name = meta.get('name')
    if not api_version or not kind or not name:
        msg = 'manifest must include apiVersion, kind, metadata.name'
        raise ValueError(msg)

    resource = k8s_get_resource(dyn, api_version, kind)
    kwargs = k8s_build_kwargs(resource, meta)

    if not k8s_resource_exists(resource, name, kwargs):
        created = resource.create(body=document, **kwargs)
        return 'created', _to_dict(created)

    current = resource.get(name=name, **kwargs)
    current_dict = _to_dict(current)
    pre_rv = (current_dict.get('metadata') or {}).get('resourceVersion')

    patched = k8s_try_patch_manifest(resource, name, document, field_manager, kwargs, pre_rv=pre_rv)
    if patched is not None:
        return patched

    recreated = await k8s_recreate_manifest(resource, name, document, kwargs)
    return 'recreated', recreated


def k8s_expand_manifest(doc: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if doc.get('kind') == 'List' and isinstance(doc.get('items'), list):
        return (i for i in doc['items'] if isinstance(i, dict))
    return (doc,)


async def k8s_apply_manifests(
    result: DeploymentResult,
    config: KonaGlobalConfig,
    path: Path,
    deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig,
) -> None:
    clusters_count = len(config.clusters)

    for manifest in deployment_config.kubernetes_manifests:
        if clusters_count != 1 and not manifest.cluster_name:
            msg = 'Cluster name should always be defined when there are more than one cluster defined'
            raise ValueError(msg)

        if manifest.cluster_name:
            load_kubeconfig(config, manifest.cluster_name)

        dyn = DynamicClient(ApiClient())

        for manifest_path_str in manifest.paths:
            for doc in yaml.safe_load_all((path / manifest_path_str).read_text()):
                for item in k8s_expand_manifest(doc):
                    try:
                        action, obj = await k8s_upsert_manifest(dyn, item)
                    except Exception as e:
                        ctx = f'{item.get("apiVersion")}/{item.get("kind")} {item.get("metadata", {}).get("name")}'
                        logger.exception(f'Failed {ctx}: {e}')
                        raise

                    if k8s_is_crd(item) and action != 'unchanged':
                        await k8s_wait_crd_established(dyn, item['metadata']['name'])
                        k8s_refresh_discovery(dyn)

                    meta = (obj or item).get('metadata', {})
                    api_version = item.get('apiVersion')
                    kind = item.get('kind')
                    name = meta.get('name', '<unnamed>')
                    ns = meta.get('namespace')
                    scope = f'{ns}/' if ns else ''
                    logger.info(f'k8s {api_version}/{kind}:{scope}{name} {action}')

                    enriched = dict(item)
                    enriched['_action'] = action
                    result.deployed_kubernetes_manifests.append(enriched)


async def deploy_challenge(
    config: KonaGlobalConfig, path: Path, deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig
) -> DeploymentResult:
    result = DeploymentResult()
    await docker_build_images(result, config, path, deployment_config)
    await k8s_apply_manifests(result, config, path, deployment_config)
    return result
