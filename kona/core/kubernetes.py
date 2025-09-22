import io
from dataclasses import dataclass

from kubernetes.config import load_incluster_config, load_kube_config
from loguru import logger

from kona.schema.models import KonaGlobalConfig


@dataclass
class KubernetesState:
    last_loaded_cluster: str | None = None


kubernetes_state = KubernetesState()


def load_kubeconfig(global_config: KonaGlobalConfig, cluster_name: str) -> None:
    if kubernetes_state.last_loaded_cluster is not None and kubernetes_state.last_loaded_cluster == cluster_name:
        return

    cluster = global_config.clusters.get(cluster_name)
    if cluster is None:
        msg = f'Unknown cluster "{cluster_name}"'
        raise ValueError(msg)

    if not cluster.kubeconfig:
        logger.info(f'Loaded incluster config for cluster "{cluster_name}"')
        load_incluster_config()
        return

    kubeconfig = cluster.kubeconfig.load(global_config)
    load_kube_config(
        config_file=io.BytesIO(kubeconfig.encode()),
    )
    logger.info(f'Loaded kubeconfig for cluster "{cluster_name}"')
