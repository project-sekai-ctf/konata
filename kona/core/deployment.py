import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from kona.util.jinja import render_template, render_template_values

from .kubernetes import load_kubeconfig, resolve_cluster_names


DEFAULT_NAMESPACE = 'default'


@dataclass(frozen=True)
class BuiltDockerImage:
    path: Path
    full_ref: str
    digest: str | None = None


@dataclass
class DeploymentResult:
    deployed_kubernetes_manifests: list[dict] = field(default_factory=list)
    built_docker_images: list[BuiltDockerImage] = field(default_factory=list)


@cache
def docker_env() -> docker.DockerClient:
    return docker.from_env()


def docker_build_image(
    env: docker.DockerClient,
    context_dir: Path,
    full_ref: str,
    build_args: dict,
    platform: str | None,
    *,
    no_cache: bool,
) -> None:
    _, logs = env.images.build(
        path=str(context_dir),
        tag=full_ref,
        nocache=no_cache,
        pull=True,
        forcerm=True,
        buildargs=build_args,
        platform=platform,
    )
    for line in logs:
        logger.debug(str(line).strip())


def docker_push_image(env: docker.DockerClient, repository: str, tag: str) -> str | None:
    # First try without auth_config so credential helpers (e.g. gcloud) are used.
    # Fall back to auth_config={} for anonymous pushes — needed since Docker 28.3.3
    # which rejects pushes without an X-Registry-Auth header.
    # https://github.com/docker/docker-py/issues/3348#issuecomment-3224418755
    push_kwargs: dict[str, Any] = {}

    for attempt in range(2):
        error: str | None = None
        digest: str | None = None
        for line in env.images.push(
            repository=repository,
            tag=tag,
            stream=True,
            decode=True,
            **push_kwargs,
        ):
            if 'error' in line:
                error = line['error']
                break
            if 'aux' in line:
                digest = line['aux'].get('Digest')
            logger.debug(str(line).strip())

        if error is None:
            return digest

        if attempt == 0 and 'IncompleteRead' in error:
            logger.debug('Retrying push with empty auth_config (Docker 28.3.3+ workaround)')
            push_kwargs['auth_config'] = {}
            continue

        raise RuntimeError(error)
    return None


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
        full_path = (path / image.path).resolve().absolute()

        logger.info(f'Building {full_ref} for {image.name}:{image.tag}')

        await asyncio.to_thread(
            docker_build_image,
            env=env,
            context_dir=full_path,
            full_ref=full_ref,
            build_args=image.build_args,
            platform=image.platform,
            no_cache=image.no_cache,
        )

        digest: str | None = None
        if not image.registry_name:
            logger.warning(f'Skipping push for {full_ref} (no registry specified)')
        else:
            logger.info(f'Pushing {full_ref} for {image.name}:{image.tag}')
            digest = await asyncio.to_thread(docker_push_image, env=env, repository=repository, tag=image.tag)

        result.built_docker_images.append(
            BuiltDockerImage(
                path=full_path,
                full_ref=full_ref,
                digest=digest,
            )
        )


def _to_dict(obj: Any) -> dict[str, Any]:  # noqa: ANN401
    return obj.to_dict() if hasattr(obj, 'to_dict') else obj


def build_manifest_context(
    config: KonaGlobalConfig,
    result: DeploymentResult,
    deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig,
    challenges: list,
    *,
    use_image_digest: bool = True,
) -> dict[str, Any]:
    images: dict[str, str] = {}
    for image, built in zip(deployment_config.images, result.built_docker_images, strict=False):
        ref = built.full_ref
        if use_image_digest and built.digest:
            ref = f'{ref}@{built.digest}'
        images[image.name] = ref
    return {
        'registries': config.registries,
        'images': images,
        'config': config,
        'challenges': challenges,
    }


def _k8s_get_resource(dyn: DynamicClient, api_version: str, kind: str) -> Any:  # noqa: ANN401
    try:
        return dyn.resources.get(api_version=api_version, kind=kind)
    except ResourceNotFoundError as err:
        msg = f'Unknown resource {api_version}/{kind}. Is the CRD installed?'
        raise ValueError(msg) from err


def _k8s_apply_manifest(
    dyn: DynamicClient,
    document: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    api_version = document.get('apiVersion')
    kind = document.get('kind')
    meta = document.get('metadata') or {}
    name = meta.get('name')
    if not api_version or not kind or not name:
        msg = 'manifest must include apiVersion, kind, metadata.name'
        raise ValueError(msg)

    resource = _k8s_get_resource(dyn, api_version, kind)
    kwargs: dict[str, Any] = {}
    if getattr(resource, 'namespaced', False):
        kwargs['namespace'] = meta.get('namespace') or DEFAULT_NAMESPACE

    try:
        resource.get(name=name, **kwargs)
    except ApiException as e:
        if e.status == HTTPStatus.NOT_FOUND:
            created = resource.create(body=document, **kwargs)
            return 'created', _to_dict(created)
        raise

    # strategic merge patch
    patched = resource.patch(
        name=name,
        body=document,
        content_type='application/merge-patch+json',
        **kwargs,
    )
    return 'configured', _to_dict(patched)


def k8s_expand_manifest(doc: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if doc.get('kind') == 'List' and isinstance(doc.get('items'), list):
        return (i for i in doc['items'] if isinstance(i, dict))
    return (doc,)


def _resolve_cluster_targets(config: KonaGlobalConfig, cluster_name: str | None) -> list[str]:
    clusters_count = len(config.clusters)
    if not cluster_name:
        if clusters_count != 1:
            msg = 'Cluster name should always be defined when there are more than one cluster defined'
            raise ValueError(msg)
        return []
    return resolve_cluster_names(config, cluster_name)


async def _k8s_apply_items_to_cluster(
    result: DeploymentResult,
    config: KonaGlobalConfig,
    cluster_name: str | None,
    items: list[dict[str, Any]],
) -> None:
    if cluster_name:
        load_kubeconfig(config, cluster_name)

    dyn = DynamicClient(ApiClient())

    for item in items:
        try:
            action, obj = _k8s_apply_manifest(dyn, item)
        except Exception as e:
            ctx = f'{item.get("apiVersion")}/{item.get("kind")} {item.get("metadata", {}).get("name")}'
            logger.exception(f'Failed {ctx}: {e}')
            raise

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


async def _k8s_apply_items(
    result: DeploymentResult,
    config: KonaGlobalConfig,
    cluster_name: str | None,
    items: list[dict[str, Any]],
) -> None:
    resolved = _resolve_cluster_targets(config, cluster_name)
    if resolved:
        for name in resolved:
            await _k8s_apply_items_to_cluster(result, config, name, items)
    else:
        await _k8s_apply_items_to_cluster(result, config, None, items)


def _inject_rollout_annotation(items: list[dict[str, Any]], annotation_path: str | None) -> None:
    if not annotation_path:
        return
    keys = annotation_path.split('.')
    timestamp = datetime.now(tz=UTC).isoformat()
    for item in items:
        target = item
        for key in keys:
            target = target.setdefault(key, {})
        target['konata.dev/deployed-at'] = timestamp


async def k8s_apply_manifests(
    result: DeploymentResult,
    config: KonaGlobalConfig,
    path: Path,
    deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig,
    challenges: list,
) -> None:
    for manifest in deployment_config.kubernetes_manifests:
        manifest_context = build_manifest_context(
            config, result, deployment_config, challenges, use_image_digest=manifest.rollout_restart.image
        )
        items = [
            item
            for manifest_path_str in manifest.paths
            for doc in yaml.safe_load_all(render_template((path / manifest_path_str).read_text(), **manifest_context))
            for item in k8s_expand_manifest(doc)
        ]
        _inject_rollout_annotation(items, manifest.rollout_restart.annotation_path)
        await _k8s_apply_items(result, config, manifest.cluster_name, items)


async def k8s_apply_inline_manifests(
    result: DeploymentResult,
    config: KonaGlobalConfig,
    deployment_config: KonaChallengeConfig.ChallengeDeploymentConfig,
    challenges: list,
) -> None:
    for manifest in deployment_config.kubernetes_inline_manifests:
        manifest_context = build_manifest_context(
            config, result, deployment_config, challenges, use_image_digest=manifest.rollout_restart.image
        )
        items = [
            item
            for doc in manifest.documents
            for item in k8s_expand_manifest(render_template_values(doc, **manifest_context))
        ]
        _inject_rollout_annotation(items, manifest.rollout_restart.annotation_path)
        await _k8s_apply_items(result, config, manifest.cluster_name, items)


def _postprocess_image_names(config: KonaGlobalConfig, challenge_config: KonaChallengeConfig) -> None:
    ctx: dict[str, Any] = {'challenges': challenge_config.challenges, 'config': config}
    for image in challenge_config.deployment.images:
        image.name = render_template(image.name, **ctx)


async def deploy_challenge(
    config: KonaGlobalConfig, path: Path, challenge_config: KonaChallengeConfig
) -> DeploymentResult:
    result = DeploymentResult()
    deployment_config = challenge_config.deployment
    _postprocess_image_names(config, challenge_config)

    await docker_build_images(result, config, path, deployment_config)
    challenges = challenge_config.challenges
    await k8s_apply_manifests(result, config, path, deployment_config, challenges)
    await k8s_apply_inline_manifests(result, config, deployment_config, challenges)
    return result
