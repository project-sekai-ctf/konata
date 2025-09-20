from abc import ABC, abstractmethod
from pathlib import Path

from kona.schema.models import KonaChallengeItem


class ExternalProviderABC(ABC):
    @abstractmethod
    async def setup(self) -> None:
        pass

    @abstractmethod
    async def sync_challenge(
        self, challenge: KonaChallengeItem, attachment_path: Path | None, rendered_description: str
    ) -> None:
        pass
