from loguru import logger

from kona.schema.models import KonaChallengeConfig, KonaChallengeItem, KonaEndpointType, KonaGlobalConfig

from .deployment import DeploymentResult


def _find_manifests(api_version: str, kind: str, manifests: list[dict]) -> list[dict]:
    return list(
        filter(lambda manifest: manifest.get('kind') == kind and manifest.get('apiVersion') == api_version, manifests)
    )


def discover_klodd_endpoint(
    config: KonaGlobalConfig, challenge: KonaChallengeConfig, deployment_result: DeploymentResult
) -> None:
    for klodd_challenge in _find_manifests(
        api_version='klodd.tjcsec.club/v1',
        kind='Challenge',
        manifests=deployment_result.deployed_kubernetes_manifests,
    ):
        metadata = klodd_challenge.get('metadata', {})
        challenge_name = metadata.get('name')
        if not challenge_name:
            logger.warning(f'Unable to find challenge name for {klodd_challenge=}')
            continue

        if not config.discovery.klodd_domain:
            logger.warning(f'Auto-discovered klodd challenge {challenge_name}, but klodd domain is not set!')
            continue

        endpoint = f'{config.discovery.klodd_domain}/challenge/{challenge_name}'
        logger.info(f'Discovered {challenge_name} starter endpoint {endpoint}')

        for item in challenge.challenges:
            item.endpoints.append(
                KonaChallengeItem.Endpoint(
                    name=config.discovery.klodd_endpoint_name,
                    type=KonaEndpointType.HTTPS,
                    endpoint=endpoint,
                )
            )


def discover_deployed_endpoints(
    config: KonaGlobalConfig, challenge: KonaChallengeConfig, deployment_result: DeploymentResult
) -> None:
    discover_klodd_endpoint(config, challenge, deployment_result)
