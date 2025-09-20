from pathlib import Path

import click
from loguru import logger

from .core.sync import sync
from .schema.models import KonaGlobalConfig
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
    deploy_directory_path = Path(deploy_directory).resolve().absolute()
    logger.info(f'Starting in {deploy_directory_path}')

    kona_config = load_schema(deploy_directory_path, model=KonaGlobalConfig)
    run(sync(deploy_directory_path, kona_config))


if __name__ == '__main__':
    main()
