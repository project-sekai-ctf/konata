import base64
import hashlib
from pathlib import Path
from urllib.parse import quote

from httpx import AsyncClient, Timeout
from loguru import logger

from kona.schema.models import KonaChallengeItem, KonaGlobalConfig, KonaRCTFCredentials

from .abc import ExternalProviderABC


class RCTFProvider(ExternalProviderABC):
    def __init__(self, global_config: KonaGlobalConfig, credentials: KonaRCTFCredentials) -> None:
        self.global_config = global_config
        self.credentials = credentials
        self.bearer_token: str | None = None
        self.challenges_on_remote: list[dict] = []

    @property
    def _client(self) -> AsyncClient:
        headers = {}
        if self.bearer_token is not None:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        return AsyncClient(
            base_url=str(self.credentials.base_url),
            headers=headers,
            timeout=Timeout(
                timeout=60,
            ),
        )

    async def setup(self) -> None:
        if self.bearer_token is None:
            async with self._client as client:
                r = await client.post(
                    '/api/v1/auth/login',
                    json={
                        'teamToken': self.credentials.team_token.load(global_config=self.global_config),
                    },
                )
                r.raise_for_status()
                self.bearer_token = r.json()['data']['authToken']
                logger.info('Authenticated in rCTF')

        async with self._client as client:
            r = await client.get('/api/v1/admin/challs')
            r.raise_for_status()
            self.challenges_on_remote = r.json()['data']
        logger.info(f'Retrieved {len(self.challenges_on_remote)} rCTF challenges from remote')

    async def _upload_file(self, file: Path) -> dict[str, str]:
        # If its already deployed, return the existing file info
        async with self._client as client:
            r = await client.post(
                '/api/v1/admin/upload/query',
                json={
                    'uploads': [
                        {
                            'name': file.name,
                            'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                        }
                    ]
                },
            )
            r.raise_for_status()
            upload_info = r.json()['data']
            if upload_info and upload_info[0]['url']:
                logger.info(f'File {file.name} is already uploaded to rCTF')
                return upload_info[0]

        # Otherwise, upload it
        async with self._client as client:
            logger.info(f'Uploading {file.name} to rCTF')
            b64_content = base64.b64encode(file.read_bytes()).decode()
            r = await client.post(
                '/api/v1/admin/upload',
                json={
                    'files': [
                        {
                            'name': file.name,
                            'data': f'data:application/octet-stream;base64,{b64_content}',
                        }
                    ]
                },
            )
            r.raise_for_status()
            return r.json()['data'][0]

    async def sync_challenge(
        self, challenge: KonaChallengeItem, attachment_path: Path | None, rendered_description: str
    ) -> None:
        challenge_dict = {
            'flag': challenge.flags.rctf,
            'name': challenge.name,
            'files': [],
            'author': challenge.author,
            'points': {
                'max': challenge.scoring.initial_value,
                'min': challenge.scoring.minimum_value,
            },
            'category': challenge.category,
            'description': rendered_description,
            'tiebreakEligible': challenge.scoring.rctf.eligible_for_tiebreaks,
        }

        if attachment_path is not None:
            uploaded = await self._upload_file(attachment_path)
            challenge_dict['files'] = [
                {
                    'name': uploaded['name'],
                    'url': uploaded['url'],
                }
            ]

        # TODO(es3n1n): cleanup previous attachments if changed

        try:
            existing_challenge = next(
                chal for chal in self.challenges_on_remote if chal['id'] == challenge.challenge_id
            )
            is_up_to_date = all(
                existing_challenge[attr] == challenge_dict[attr] for attr in challenge_dict if attr != 'id'
            )
        except StopIteration:
            is_up_to_date = False

        if is_up_to_date:
            logger.info(f'Challenge {challenge.challenge_id} is already up to date in rCTF')
            return

        async with self._client as client:
            r = await client.put(
                f'/api/v1/admin/challs/{quote(challenge.challenge_id)}',
                json={
                    'data': challenge_dict,
                },
            )
            r.raise_for_status()
            logger.info(f'Challenge {challenge.challenge_id} has been updated in rCTF')
