import importlib
from pathlib import Path


def include_passes() -> int:
    result = 0
    for p in Path(__file__).parent.glob('*.py'):
        if p.stem in {'__init__', 'passes'}:
            continue

        importlib.import_module(f'kona.analysis.{p.stem}')
        result += 1

    return result
