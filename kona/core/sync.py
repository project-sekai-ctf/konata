from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from loguru import logger

from kona.core.deployment import DeploymentResult, build_manifest_context, deploy_challenge
from kona.core.k8s_manifest_discovery import discover_deployed_endpoints
from kona.core.kubernetes import load_kubeconfig
from kona.core.provide import resolve_attachments, resolve_source_paths
from kona.external.abc import ExternalProviderABC
from kona.external.ctfd import CTFDProvider
from kona.external.rctf import RCTFProvider
from kona.schema.models import KonaChallengeConfig, KonaGlobalConfig
from kona.schema.parsers import try_load_schema
from kona.util.jinja import render_template, render_template_values


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
    _temp_dir: TemporaryDirectory[str] = field(
        default_factory=lambda: TemporaryDirectory(prefix='kona-sync-'),
        init=False,
        repr=False,
    )

    @property
    def temp_root(self) -> Path:
        return Path(self._temp_dir.name)

    def make_temp_dir(self, prefix: str) -> Path:
        return Path(mkdtemp(prefix=f'{prefix}-', dir=self.temp_root))

    def cleanup(self) -> None:
        self._temp_dir.cleanup()


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

    export_dir = result.make_temp_dir('exports')

    # Deploy
    deployment_result = await deploy_challenge(config, path, challenge, export_dir=export_dir)
    discover_deployed_endpoints(config, challenge, deployment_result)

    extra_entries = [(ef.path, ef.arcname) for ef in deployment_result.exported_files]
    group = SynchronizedGroup(
        deployment_result=deployment_result,
    )

    ctx = build_manifest_context(config, deployment_result, challenge.deployment, challenge.challenges)

    # Sync challenge to the providers
    for chal in challenge.challenges:
        chal.resolve_flags(path)
        ctx['challenge'] = chal

        for ep in chal.endpoints:
            ep.endpoint = render_template(ep.endpoint, **ctx)

        if chal.instancer_config is not None:
            chal.instancer_config.challenge_integration_id = render_template(
                chal.instancer_config.challenge_integration_id, **ctx
            )
            chal.instancer_config.config = render_template_values(chal.instancer_config.config, **ctx)
            for expose in chal.instancer_config.expose:
                expose.host_prefix = render_template(expose.host_prefix, **ctx)

        ctx['endpoints_rendered'] = render_template(config.templates.endpoints_text, **ctx)

        out_chal = SynchronizedChallenge()
        out_chal.description = render_template(config.templates.challenge_description, **ctx)
        out_chal.attachments = resolve_source_paths(path, chal.attachments)

        with TemporaryDirectory() as tmp_dir:
            attachment_paths = resolve_attachments(
                path,
                Path(tmp_dir),
                chal.attachments,
                config.attachment_format,
                chal.challenge_id,
                extra_entries=extra_entries,
            )
            if attachment_paths:
                logger.info(f'Resolved {len(attachment_paths)} attachment(s) for {chal.challenge_id}')

            for provider in external_providers:
                await provider.sync_challenge(chal, attachment_paths, out_chal.description)
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
    challenge_filter: set[Path] | None = None,
) -> None:
    if depth > config.discovery.challenge_folder_depth:
        return

    # Try load challenge schema
    if not is_root:
        challenge_schema = try_load_schema(path, model=KonaChallengeConfig)
        if challenge_schema is not None:
            if challenge_filter is not None and path.resolve() not in challenge_filter:
                logger.debug(f'Skipping {path} (not in --only filter)')
            else:
                await sync_challenge(result, config, path, challenge_schema, external_providers)

    # Look for challenges in nested folders
    for item in path.iterdir():
        try:
            if not item.is_dir():
                continue
        except OSError as err:
            logger.warning(f'Skipping folder {item} due to {err}')
            continue
        await try_discover_challenges(
            result,
            item,
            config,
            depth=depth + 1,
            external_providers=external_providers,
            challenge_filter=challenge_filter,
        )


async def setup_external_providers(config: KonaGlobalConfig) -> list[ExternalProviderABC]:
    external_providers: list[ExternalProviderABC] = []

    # rCTF
    if config.rctf is not None:
        external_providers.append(RCTFProvider(global_config=config, credentials=config.rctf))

    # CTFd
    if config.ctfd is not None:
        external_providers.append(CTFDProvider(global_config=config, credentials=config.ctfd))

    for provider in external_providers:
        await provider.setup()

    return external_providers


async def sync_challenge_paths(
    result: SyncResult,
    root_path: Path,
    config: KonaGlobalConfig,
    challenge_paths: tuple[str, ...],
    external_providers: list[ExternalProviderABC],
) -> None:
    logger.info(f'Loading {len(challenge_paths)} challenge(s) directly (skipping discovery)')
    for p in challenge_paths:
        path = (root_path / p).resolve()
        challenge_schema = try_load_schema(path, model=KonaChallengeConfig)
        if challenge_schema is None:
            logger.warning(f'No challenge config found at {path}, skipping')
            continue
        await sync_challenge(result, config, path, challenge_schema, external_providers)


async def sync(
    root_path: Path,
    config: KonaGlobalConfig,
    *,
    only_challenges: tuple[str, ...] | None = None,
    challenge_paths: tuple[str, ...] | None = None,
) -> SyncResult:
    result = SyncResult()
    try:
        external_providers = await setup_external_providers(config)

        # Set the kubeconfig if there's only one available
        if len(config.clusters) == 1:
            load_kubeconfig(config, next(iter(config.clusters.keys())))

        if challenge_paths is not None:
            await sync_challenge_paths(result, root_path, config, challenge_paths, external_providers)
            return result

        # Resolve challenge filter
        challenge_filter: set[Path] | None = None
        if only_challenges is not None:
            challenge_filter = {(root_path / p).resolve() for p in only_challenges}
            logger.info(f'Filtering to {len(challenge_filter)} challenge(s): {", ".join(only_challenges)}')

        # Discover challenges
        await try_discover_challenges(
            result,
            root_path,
            config,
            external_providers=external_providers,
            is_root=True,
            challenge_filter=challenge_filter,
        )
    except Exception:
        result.cleanup()
        raise
    else:
        return result
