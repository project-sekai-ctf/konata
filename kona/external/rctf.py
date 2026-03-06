import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from httpx import AsyncClient, Timeout
from loguru import logger

from kona.schema.models import KonaChallengeItem, KonaGlobalConfig, KonaRCTFCredentials
from kona.util.http import raise_for_status

from .abc import ExternalProviderABC


class RCTFProvider(ExternalProviderABC):
    def __init__(self, global_config: KonaGlobalConfig, credentials: KonaRCTFCredentials) -> None:
        self.global_config = global_config
        self.credentials = credentials
        self.bearer_token: str | None = None
        self.challenges_on_remote: list[dict] = []

    @property
    def _client(self) -> AsyncClient:
        headers = dict(self.credentials.extra_headers)
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
                raise_for_status(r)
                self.bearer_token = r.json()['data']['authToken']
                logger.info('Authenticated in rCTF')

        async with self._client as client:
            r = await client.get('/api/v1/admin/challs')
            raise_for_status(r)
            self.challenges_on_remote = r.json()['data']
        logger.info(f'Retrieved {len(self.challenges_on_remote)} rCTF challenges from remote')

    async def _upload_file(self, file: Path) -> dict[str, str]:
        # If its already deployed, return the existing file info
        async with self._client as client:
            r = await client.post(
                '/api/v2/admin/upload/query',
                json={
                    'uploads': [
                        {
                            'name': file.name,
                            'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                        }
                    ]
                },
            )
            raise_for_status(r)
            upload_info = r.json()['data']
            if upload_info and upload_info[0]['url']:
                logger.info(f'File {file.name} is already uploaded to rCTF')
                return upload_info[0]

        # Otherwise, upload it
        async with self._client as client:
            logger.info(f'Uploading {file.name} to rCTF')
            r = await client.post(
                '/api/v2/admin/upload',  # v2
                files={
                    'files': (file.name, BytesIO(file.read_bytes()), 'application/binary'),
                },
            )
            raise_for_status(r)
            return r.json()['data'][0]

    async def sync_challenge(
        self, challenge: KonaChallengeItem, attachment_paths: list[Path], rendered_description: str
    ) -> None:
        uploaded_files: list[dict[str, str]] = [
            await self._upload_file(attachment_path) for attachment_path in attachment_paths
        ]
        challenge_dict: dict[str, Any] = {
            'flag': challenge.flags.rctf,
            'name': challenge.name,
            'files': uploaded_files,
            'author': challenge.author,
            'points': {
                'max': challenge.scoring.initial_value,
                'min': challenge.scoring.minimum_value,
            },
            'category': challenge.category,
            'description': rendered_description,
            'tiebreakEligible': challenge.scoring.rctf.eligible_for_tiebreaks,
            'hidden': challenge.hidden,
            'sortWeight': challenge.sort_weight or 0,
        }

        if challenge.instancer_config is not None:
            challenge_dict['instancerConfig'] = {
                'challengeIntegrationId': challenge.instancer_config.challenge_integration_id,
                'config': challenge.instancer_config.config,
                'expose': [
                    {
                        'kind': expose.kind.value,
                        'hostPrefix': expose.host_prefix,
                        'containerName': expose.container_name,
                        'containerPort': expose.container_port,
                        'shouldDisplay': expose.should_display,
                        **(({'title': expose.title}) if expose.title is not None else {}),
                    }
                    for expose in challenge.instancer_config.expose
                ],
                'timeoutMilliseconds': challenge.instancer_config.timeout_milliseconds,
                'extendable': challenge.instancer_config.extendable,
            }
        else:
            challenge_dict['instancerConfig'] = None

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
                f'/api/v2/admin/challs/{quote(challenge.challenge_id)}',  # v2
                json={
                    'data': challenge_dict,
                },
            )
            raise_for_status(r)
            logger.info(f'Challenge {challenge.challenge_id} has been updated in rCTF')
