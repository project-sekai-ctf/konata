from pathlib import Path

import click
from loguru import logger

from kona.analysis import include_passes
from kona.analysis.passes import AnalysisContext, passes
from kona.schema.models import AttachmentFormat
from kona.util.tar import make_tar_gz_from
from kona.util.zip import make_zip

from .core.sync import sync
from .schema.models import KonaGlobalConfig, kona_global_state
from .schema.parsers import load_schema


try:
    from uvloop import run  # type: ignore[import-not-found]
except ImportError:
    from asyncio import run  # type: ignore[no-redef]


async def job(
    deploy_directory: str,
    only: tuple[str, ...] = (),
    challenge_paths: tuple[str, ...] = (),
) -> None:
    kona_global_state.root_path = Path(deploy_directory).resolve().absolute()
    logger.info(f'Starting in {kona_global_state.root_path}')
    logger.info(f'Included {include_passes()} passes')

    only_challenges: tuple[str, ...] | None = only if only else None
    resolved_paths: tuple[str, ...] | None = challenge_paths if challenge_paths else None
    kona_config = load_schema(kona_global_state.root_path, model=KonaGlobalConfig)
    sync_result = await sync(
        kona_global_state.root_path,
        kona_config,
        only_challenges=only_challenges,
        challenge_paths=resolved_paths,
    )

    context = AnalysisContext(
        global_config=kona_config,
        sync_result=sync_result,
    )
    for analysis_pass in passes:
        logger.info(f'Running pass {analysis_pass.__name__}')
        await analysis_pass(context)

    logger.info('We are done here')


@click.group()
def main() -> None:
    pass


@main.command()
@click.option(
    '-d',
    '--deploy-directory',
    'deploy_directory',
    type=click.Path(exists=True, file_okay=False),
    required=True,
)
@click.option(
    '--only',
    'only',
    multiple=True,
    help='Only sync specific challenge directories (paths relative to deploy-directory).',
)
@click.option(
    '--challenge-path',
    'challenge_paths',
    multiple=True,
    help='Direct paths to challenge directories (skips discovery).',
)
@logger.catch(reraise=True)
def sync_cmd(deploy_directory: str, only: tuple[str, ...], challenge_paths: tuple[str, ...]) -> None:
    run(job(deploy_directory, only=only, challenge_paths=challenge_paths))


@main.command('compress')
@click.argument('path', type=click.Path(exists=True))
@click.option(
    '-f',
    '--format',
    'fmt',
    type=click.Choice(['tar_gz', 'zip'], case_sensitive=False),
    default='tar_gz',
    show_default=True,
)
@click.option(
    '-o',
    '--output',
    'output_path',
    type=click.Path(),
    default=None,
)
@logger.catch(reraise=True)
def compress_cmd(path: str, fmt: str, output_path: str | None) -> None:
    source = Path(path).resolve()
    attachment_fmt = AttachmentFormat(fmt)

    if output_path is None:
        ext = '.zip' if attachment_fmt == AttachmentFormat.ZIP else '.tar.gz'
        output = Path.cwd() / f'{source.name}{ext}'
    else:
        output = Path(output_path).resolve()

    if source.is_dir():
        all_files = [p for p in source.rglob('*') if p.is_file()]
        entries = [(p, str(p.relative_to(source))) for p in all_files]
    else:
        entries = [(source, source.name)]

    if not entries:
        logger.warning('No files to compress')
        return

    if attachment_fmt == AttachmentFormat.ZIP:
        make_zip(output, entries)
    else:
        make_tar_gz_from(output, entries)

    logger.info(f'Compressed {len(entries)} file(s) to {output}')


if __name__ == '__main__':
    main()
