from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from loguru import logger

from kona.core.deployment import DeploymentResult, deploy_challenge
from kona.core.k8s_manifest_discovery import discover_deployed_endpoints
from kona.core.kubernetes import load_kubeconfig
from kona.external.abc import ExternalProviderABC
from kona.external.ctfd import CTFDProvider
from kona.external.rctf import RCTFProvider
from kona.schema.models import KonaChallengeConfig, KonaGlobalConfig
from kona.schema.parsers import try_load_schema
from kona.util.jinja import render_template
from kona.util.tar import make_tar_gz


@dataclass
class SynchronizedChallenge:
    description: str = ''
    attachments: list[Path] = field(default_factory=list)


@dataclass
class SynchronizedGroup:
    deployment_result: DeploymentResult
    challenges: list[SynchronizedChallenge] = field(default_factory=list)


@dataclass
class SyncResult:
    groups: list[SynchronizedGroup] = field(default_factory=list)


async def sync_challenge(
    result: SyncResult,
    config: KonaGlobalConfig,
    path: Path,
    challenge: KonaChallengeConfig,
    external_providers: list[ExternalProviderABC],
) -> None:
    logger.info(f'Discovered challenge(s) at {path}: {", ".join(chal.challenge_id for chal in challenge.challenges)}')
    if challenge.discovery.skip:
        logger.warning(f'Skipping {path}')
        return

    # Deploy
    deployment_result = await deploy_challenge(config, path, challenge.deployment)
    discover_deployed_endpoints(config, challenge, deployment_result)

    group = SynchronizedGroup(
        deployment_result=deployment_result,
    )

    # Sync challenge to the providers
    for chal in challenge.challenges:
        out_chal = SynchronizedChallenge()
        out_chal.description = render_template(
            config.templates.challenge_description,
            challenge=chal,
            endpoints_rendered=render_template(config.templates.endpoints_text, challenge=chal),
        )
        out_chal.attachments = [(path / item) for item in chal.attachments]

        with TemporaryDirectory() as tmp_dir:
            attachments_path: Path | None = None
            if chal.attachments:
                # TODO(es3n1n): instead of doing challenge id maybe render a template here?
                attachments_path = Path(tmp_dir) / f'{chal.challenge_id}.tar.gz'
                make_tar_gz(
                    attachments_path,
                    out_chal.attachments,
                )
                logger.info(f'Created attachments tarball at {attachments_path}')

            for provider in external_providers:
                await provider.sync_challenge(chal, attachments_path, out_chal.description)
                # challenges were updated, refresh the local cache
                await provider.setup()

        group.challenges.append(out_chal)

    result.groups.append(group)


async def try_discover_challenges(
    result: SyncResult,
    path: Path,
    config: KonaGlobalConfig,
    *,
    depth: int = 0,
    is_root: bool = False,
    external_providers: list[ExternalProviderABC],
) -> None:
    if depth > config.discovery.challenge_folder_depth:
        return

    # Try load challenge schema
    if not is_root:
        challenge_schema = try_load_schema(path, model=KonaChallengeConfig)
        if challenge_schema is not None:
            await sync_challenge(result, config, path, challenge_schema, external_providers)

    # Look for challenges in nested folders
    for item in path.iterdir():
        if not item.is_dir():
            continue
        await try_discover_challenges(result, item, config, depth=depth + 1, external_providers=external_providers)


async def sync(root_path: Path, config: KonaGlobalConfig) -> SyncResult:
    result = SyncResult()
    external_providers: list[ExternalProviderABC] = []

    # rCTF
    if config.rctf is not None:
        external_providers.append(RCTFProvider(global_config=config, credentials=config.rctf))

    # CTFd
    if config.ctfd is not None:
        external_providers.append(CTFDProvider(global_config=config, credentials=config.ctfd))

    # Setup external providers
    for provider in external_providers:
        await provider.setup()

    # Set the kubeconfig if there's only one available
    if len(config.clusters) == 1:
        load_kubeconfig(config, next(iter(config.clusters.keys())))

    # Discover challenges
    await try_discover_challenges(result, root_path, config, external_providers=external_providers, is_root=True)
    return result
