import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import yaml
from pydantic import TypeAdapter


if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

T = TypeVar('T', bound='BaseModel')


def load_yaml[T](file_path: Path, model: type[T]) -> T:
    return TypeAdapter(model).validate_python(
        yaml.safe_load(file_path.read_bytes()),
    )


def load_toml[T](file_path: Path, model: type[T]) -> T:
    return TypeAdapter(model).validate_python(
        tomllib.loads(file_path.read_bytes().decode('utf-8')),
    )


def load_schema[T](
    directory: Path,
    *,
    file_name: str = 'kona',
    model: type[T],
) -> T:
    for ext in ('yaml', 'yml', 'toml'):
        loader: Callable[[Path, type[T]], T] = load_yaml if ext in ('yaml', 'yml') else load_toml
        file_path = directory / f'{file_name}.{ext}'

        if not file_path.exists():
            continue

        return loader(file_path, model)

    msg = f'No supported schema file found for {file_name} in {directory}'
    raise FileNotFoundError(msg)


def try_load_schema[T](
    directory: Path,
    *,
    file_name: str = 'kona',
    model: type[T],
) -> T | None:
    try:
        return load_schema(directory, file_name=file_name, model=model)
    except FileNotFoundError:
        return None
