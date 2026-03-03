import io
import subprocess
from dataclasses import dataclass

from kubernetes.config import load_incluster_config, load_kube_config
from loguru import logger

from kona.schema.models import KonaGlobalConfig


@dataclass
class KubernetesState:
    last_loaded_cluster: str | None = None


kubernetes_state = KubernetesState()


def _run_checked(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        msg = f'Command {args} failed (exit {result.returncode}): {output}'
        raise RuntimeError(msg)
    return result.stdout


def _load_gcloud_credentials(cluster_name: str, project: str, zone: str) -> None:
    _run_checked(
        [
            'gcloud',
            'container',
            'clusters',
            'get-credentials',
            cluster_name,
            '--project',
            project,
            '--zone',
            zone,
        ]
    )
    logger.info(f'Loaded gcloud credentials for cluster "{cluster_name}" in {project}/{zone}')


def _load_kind_credentials(cluster_name: str) -> None:
    _run_checked(['kind', 'export', 'kubeconfig', '--name', cluster_name])
    logger.info(f'Loaded kind credentials for cluster "{cluster_name}"')


def resolve_cluster_names(global_config: KonaGlobalConfig, cluster_name: str) -> list[str]:
    cluster = global_config.clusters.get(cluster_name)
    if cluster is None:
        msg = f'Unknown cluster "{cluster_name}"'
        raise ValueError(msg)

    if cluster.alias_to is None:
        return [cluster_name]

    targets = [cluster.alias_to] if isinstance(cluster.alias_to, str) else cluster.alias_to
    resolved: list[str] = []
    for target in targets:
        resolved.extend(resolve_cluster_names(global_config, target))
    return resolved


def _load_kubeconfig_single(global_config: KonaGlobalConfig, cluster_name: str) -> None:
    cluster = global_config.clusters.get(cluster_name)
    if cluster is None:
        msg = f'Unknown cluster "{cluster_name}"'
        raise ValueError(msg)

    if cluster.alias_to is not None:
        msg = f'Cluster "{cluster_name}" is an alias, resolve it first'
        raise ValueError(msg)

    if cluster.gcloud:
        _load_gcloud_credentials(cluster.gcloud.cluster_name, cluster.gcloud.project, cluster.gcloud.zone)
        load_kube_config()
        return

    if cluster.kind:
        _load_kind_credentials(cluster.kind.cluster_name)
        load_kube_config()
        return

    if not cluster.kubeconfig:
        if cluster.incluster:
            load_incluster_config()
            logger.info(f'Loaded incluster config for cluster "{cluster_name}"')
            return

        if cluster.use_default:
            load_kube_config()
            logger.info(f'Loaded default config for cluster "{cluster_name}"')
            return

        msg = f'Unable to load config for cluster "{cluster_name}"'
        raise ValueError(msg)

    kubeconfig = cluster.kubeconfig.load(global_config)
    load_kube_config(
        config_file=io.BytesIO(kubeconfig.encode()),
    )
    logger.info(f'Loaded kubeconfig for cluster "{cluster_name}"')


def load_kubeconfig(global_config: KonaGlobalConfig, cluster_name: str) -> None:
    if kubernetes_state.last_loaded_cluster is not None and kubernetes_state.last_loaded_cluster == cluster_name:
        return

    _load_kubeconfig_single(global_config, cluster_name)
    kubernetes_state.last_loaded_cluster = cluster_name
