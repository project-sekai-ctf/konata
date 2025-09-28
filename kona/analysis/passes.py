from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from kona.core.sync import SyncResult
from kona.schema.models import KonaGlobalConfig


@dataclass
class AnalysisContext:
    global_config: KonaGlobalConfig
    sync_result: SyncResult


passes: list[Callable[[AnalysisContext], Awaitable[None]]] = []


def analysis_pass(cb: Callable[[AnalysisContext], Awaitable[None]]) -> Callable[[AnalysisContext], Awaitable[None]]:
    passes.append(cb)
    return cb
