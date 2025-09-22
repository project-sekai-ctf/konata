from pathlib import Path
from tempfile import TemporaryDirectory

from loguru import logger

from kona.core.deployment import deploy_challenge
from kona.core.kubernetes import load_kubeconfig
from kona.external.abc import ExternalProviderABC
from kona.external.ctfd import CTFDProvider
from kona.external.rctf import RCTFProvider
from kona.schema.models import KonaChallengeConfig, KonaGlobalConfig
from kona.schema.parsers import try_load_schema
from kona.util.jinja import render_template
from kona.util.tar import make_tar_gz


async def sync_challenge(
    config: KonaGlobalConfig, path: Path, challenge: KonaChallengeConfig, external_providers: list[ExternalProviderABC]
) -> None:
    logger.info(f'Discovered challenge(s) at {path}: {", ".join(chal.challenge_id for chal in challenge.challenges)}')
    if challenge.discovery.skip:
        logger.warning(f'Skipping {path}')
        return

    # Sync challenge to the providers
    for chal in challenge.challenges:
        description = render_template(
            config.templates.challenge_description,
            challenge=chal,
            endpoints_rendered=render_template(config.templates.endpoints_text, challenge=chal),
        )

        with TemporaryDirectory() as tmp_dir:
            attachments_path: Path | None = None
            if chal.attachments:
                # TODO(es3n1n): instead of doing challenge id maybe render a template here?
                attachments_path = Path(tmp_dir) / f'{chal.challenge_id}.tar.gz'
                make_tar_gz(
                    attachments_path,
                    [(path / item) for item in chal.attachments],
                )
                logger.info(f'Created attachments tarball at {attachments_path}')

            for provider in external_providers:
                await provider.sync_challenge(chal, attachments_path, description)
                # challenges were updated, refresh the local cache
                await provider.setup()

    # Deploy
    await deploy_challenge(config, path, challenge.deployment)


async def try_discover_challenges(
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
            await sync_challenge(config, path, challenge_schema, external_providers)

    # Look for challenges in nested folders
    for item in path.iterdir():
        if not item.is_dir():
            continue
        await try_discover_challenges(item, config, depth=depth + 1, external_providers=external_providers)


async def sync(root_path: Path, config: KonaGlobalConfig) -> None:
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
    await try_discover_challenges(root_path, config, external_providers=external_providers, is_root=True)
