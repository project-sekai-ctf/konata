from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger

from kona.schema.models import KonaChallengeItem


class ExternalProviderABC(ABC):
    kind: str
    display_name: str
    challenges_on_remote: list[dict]
    synced_remote_ids: set[str | int]

    @abstractmethod
    async def setup(self) -> None:
        pass

    @abstractmethod
    async def sync_challenge(
        self, challenge: KonaChallengeItem, attachment_paths: list[Path], rendered_description: str
    ) -> None:
        pass

    def report_untracked_challenges(self) -> None:
        untracked = [chal for chal in self.challenges_on_remote if chal['id'] not in self.synced_remote_ids]
        if not untracked:
            return

        labels = ', '.join(
            f'{chal["category"]}/{chal["name"]}' for chal in sorted(untracked, key=lambda c: (c['category'], c['name']))
        )
        logger.warning(f'{self.display_name} has {len(untracked)} untracked challenge(s): {labels}')
