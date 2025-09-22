from pathlib import Path

import click
from loguru import logger

from .core.sync import sync
from .schema.models import KonaGlobalConfig, kona_global_state
from .schema.parsers import load_schema


try:
    from uvloop import run  # type: ignore[import-not-found]
except ImportError:
    from asyncio import run  # type: ignore[no-redef]


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
    kona_global_state.root_path = Path(deploy_directory).resolve().absolute()
    logger.info(f'Starting in {kona_global_state.root_path}')

    kona_config = load_schema(kona_global_state.root_path, model=KonaGlobalConfig)
    run(sync(kona_global_state.root_path, kona_config))


if __name__ == '__main__':
    main()
