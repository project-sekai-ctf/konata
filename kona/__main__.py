from pathlib import Path

import click
from loguru import logger

from kona.analysis import include_passes
from kona.analysis.passes import AnalysisContext, passes

from .core.sync import sync
from .schema.models import KonaGlobalConfig, kona_global_state
from .schema.parsers import load_schema


try:
    from uvloop import run  # type: ignore[import-not-found]
except ImportError:
    from asyncio import run  # type: ignore[no-redef]


async def job(deploy_directory: str) -> None:
    kona_global_state.root_path = Path(deploy_directory).resolve().absolute()
    logger.info(f'Starting in {kona_global_state.root_path}')
    logger.info(f'Included {include_passes()} passes')

    kona_config = load_schema(kona_global_state.root_path, model=KonaGlobalConfig)
    sync_result = await sync(kona_global_state.root_path, kona_config)

    context = AnalysisContext(
        global_config=kona_config,
        sync_result=sync_result,
    )
    for analysis_pass in passes:
        logger.info(f'Running pass {analysis_pass.__name__}')
        await analysis_pass(context)

    logger.info('We are done here')


@logger.catch
@click.command()
@click.option(
    '-d',
    '--deploy-directory',
    'deploy_directory',
    type=click.Path(exists=True, file_okay=False),
    required=True,
)
def main(deploy_directory: str) -> None:
    run(job(deploy_directory))


if __name__ == '__main__':
    main()
