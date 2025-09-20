import hashlib
from pathlib import Path

from httpx import AsyncClient
from loguru import logger

from kona.schema.models import KonaChallengeItem, KonaCTFDCredentials, KonaGlobalConfig
from kona.util.jinja import render_template

from .abc import ExternalProviderABC


def flag_to_ctfd(challenge_id: int, flag: KonaChallengeItem.Flags.CTFDFlag) -> dict:
    return {
        'content': flag.flag,
        'type': flag.type,
        'challenge_id': challenge_id,
    }


def topic_to_ctfd(challenge_id: int, topic: str) -> dict:
    return {
        'value': topic,
        'type': 'challenge',
        'challenge_id': challenge_id,
    }


def tag_to_ctfd(challenge_id: int, tag: str) -> dict:
    return {
        'value': tag,
        'challenge_id': challenge_id,
    }


def hint_to_ctfd(challenge_id: int, hint: KonaChallengeItem.CTFD.Hint) -> dict:
    return {
        'content': hint.hint,
        'title': hint.title,
        'cost': hint.cost,
        'challenge_id': challenge_id,
    }


def filter_items(
    on_server: list[dict], local: list[dict], keys: list[str] | None = None
) -> tuple[list[dict], list[dict]]:
    to_add: list[dict] = []
    to_remove: list[dict] = []

    if not local:
        return to_add, on_server

    if not on_server:
        return local, to_remove

    if keys is None:
        keys = list(local[0].keys())

    to_add = [
        local_item
        for local_item in local
        if not any(all(local_item[k] == server_item[k] for k in keys) for server_item in on_server)
    ]
    to_remove = [
        server_item
        for server_item in on_server
        if not any(all(server_item[k] == local_item[k] for k in keys) for local_item in local)
    ]
    return to_add, to_remove


class CTFDProvider(ExternalProviderABC):
    def __init__(self, global_config: KonaGlobalConfig, credentials: KonaCTFDCredentials) -> None:
        self.global_config = global_config
        self.credentials = credentials
        self.admin_token: str = credentials.admin_token.load(global_config=global_config)
        self.challenges_on_remote: list[dict] = []

    @property
    def _client(self) -> AsyncClient:
        return AsyncClient(
            base_url=str(self.credentials.base_url),
            headers={
                'Authorization': f'Token {self.admin_token}',
                'Content-Type': 'application/json',  # very important, lol...
            },
        )

    async def setup(self) -> None:
        async with self._client as client:
            r = await client.get('/api/v1/challenges?view=admin')
            r.raise_for_status()
            self.challenges_on_remote = r.json()['data']
        logger.info(f'Retrieved {len(self.challenges_on_remote)} CTFd challenges from remote')

    async def _load_full_challenge(self, challenge_id: int) -> dict:
        async with self._client as client:
            r = await client.get(f'/api/v1/challenges/{challenge_id}?view=admin')
            r.raise_for_status()
            return r.json()['data']

    async def _create_challenge(
        self, challenge: KonaChallengeItem, attachment_path: Path | None, challenge_dict: dict
    ) -> None:
        async with self._client as client:
            r = await client.post(
                '/api/v1/challenges',
                json=challenge_dict,
            )
            r.raise_for_status()
            challenge_id = r.json()['data']['id']
            logger.info(f'Created challenge {challenge.name} in CTFd with ID {challenge_id}')

            # flags
            for flag in challenge.flags.ctfd:
                r = await client.post(
                    '/api/v1/flags',
                    json=flag_to_ctfd(challenge_id, flag),
                )
                r.raise_for_status()

            # topics
            for topic in challenge.ctfd.topics:
                r = await client.post(
                    '/api/v1/topics',
                    json=topic_to_ctfd(challenge_id, topic),
                )
                r.raise_for_status()

            # tags
            for tag in challenge.ctfd.tags:
                r = await client.post(
                    '/api/v1/tags',
                    json=tag_to_ctfd(challenge_id, tag),
                )
                r.raise_for_status()

            # hints
            for hint in challenge.ctfd.hints:
                r = await client.post(
                    '/api/v1/hints',
                    json=hint_to_ctfd(challenge_id, hint),
                )
                r.raise_for_status()

        # This **needs** to be without Content-Type header, let httpx set it properly with multipart boundary
        async with self._client as client:
            client.headers.pop('Content-Type', None)

            # files
            if attachment_path is not None:
                r = await client.post(
                    '/api/v1/files',
                    data={
                        'challenge_id': challenge_id,
                        'type': 'challenge',
                    },
                    files={'file': (attachment_path.name, attachment_path.read_bytes())},
                )
                r.raise_for_status()
                if len(r.json()['data']) < 1:
                    raise RuntimeError

    async def _update_challenge(
        self, challenge: KonaChallengeItem, attachment_path: Path | None, challenge_dict: dict, current_challenge: dict
    ) -> None:
        # Initial patch if needed
        if any(challenge_dict[x] != current_challenge[x] for x in challenge_dict):
            async with self._client as client:
                r = await client.patch(
                    f'/api/v1/challenges/{current_challenge["id"]}',
                    json=challenge_dict,
                )
                r.raise_for_status()
                logger.info(f'Updated challenge {challenge.challenge_id} in CTFd with ID {current_challenge["id"]}')

        async def generic_updater_inner(
            name: str,
            _client: AsyncClient,
            add_url: str,
            remove_url: str,
            remote_items: list[dict],
            local_items: list[dict],
            keys: list[str] | None = None,
        ) -> None:
            add_items, remove_items = filter_items(
                remote_items,
                local_items,
                keys=keys,
            )

            for a_item in add_items:
                g_r = await _client.post(
                    add_url,
                    json=a_item,
                )
                g_r.raise_for_status()
                logger.info(
                    f'Added new {name} {a_item} to {challenge.challenge_id} in CTFd with ID {current_challenge["id"]}'
                )

            for a_item in remove_items:
                g_r = await _client.delete(remove_url.format(id=a_item['id']))
                g_r.raise_for_status()
                logger.info(
                    f'Removed {name} {a_item} from {challenge.challenge_id} in CTFd with ID {current_challenge["id"]}'
                )

        async def generic_updater(
            name: str,
            get_url: str,
            add_url: str,
            remove_url: str,
            local_items: list[dict],
            keys: list[str] | None = None,
        ) -> None:
            async with self._client as _client:
                g_r = await _client.get(get_url)
                g_r.raise_for_status()
                await generic_updater_inner(name, _client, add_url, remove_url, g_r.json()['data'], local_items, keys)

        # flags
        await generic_updater(
            name='flag',
            get_url=f'/api/v1/flags?challenge_id={current_challenge["id"]}',
            add_url='/api/v1/flags',
            remove_url='/api/v1/flags/{id}',
            local_items=[flag_to_ctfd(current_challenge['id'], flag) for flag in challenge.flags.ctfd],
        )

        # topics
        await generic_updater(
            name='topic',
            get_url=f'/api/v1/topics?challenge_id={current_challenge["id"]}',
            add_url='/api/v1/topics',
            remove_url='/api/v1/topics/{id}',
            local_items=[topic_to_ctfd(current_challenge['id'], topic) for topic in challenge.ctfd.topics],
            keys=['value'],
        )

        # tags
        await generic_updater(
            name='tag',
            get_url=f'/api/v1/tags?challenge_id={current_challenge["id"]}',
            add_url='/api/v1/tags',
            remove_url='/api/v1/tags/{id}',
            local_items=[tag_to_ctfd(current_challenge['id'], tag) for tag in challenge.ctfd.tags],
            keys=['value'],
        )

        # hints are a bit special
        async with self._client as _client:
            r = await _client.get(f'/api/v1/hints?challenge_id={current_challenge["id"]}')
            r.raise_for_status()
            server_hints: list[dict] = r.json()['data']
            for item in server_hints:
                r = await _client.get(f'/api/v1/hints/{item["id"]}?preview=true')
                r.raise_for_status()
                item['content'] = r.json()['data']['content']

            await generic_updater_inner(
                name='hint',
                _client=_client,
                add_url='/api/v1/hints',
                remove_url='/api/v1/hints/{id}',
                remote_items=server_hints,
                local_items=[hint_to_ctfd(current_challenge['id'], hint) for hint in challenge.ctfd.hints],
                keys=['content', 'title', 'cost'],
            )

        # files are special too
        async with self._client as _client:
            r = await _client.get(f'/api/v1/challenges/{current_challenge["id"]}/files')
            r.raise_for_status()

            server_files: list[dict] = r.json()['data']
            local_files: list[dict] = (
                [
                    {
                        'sha1sum': hashlib.sha1(attachment_path.read_bytes(), usedforsecurity=False).hexdigest(),
                        'name': attachment_path.name,
                        'data': attachment_path.read_bytes(),
                    }
                ]
                if attachment_path
                else []
            )

            add_files, remove_files = filter_items(server_files, local_files, keys=['sha1sum'])
            for remove_file in remove_files:
                r = await _client.delete(f'/api/v1/files/{remove_file["id"]}')
                r.raise_for_status()
                logger.info(
                    f'Removed file {remove_file} from {challenge.challenge_id} in CTFd '
                    f'with ID {current_challenge["id"]}'
                )

            # This **needs** to be without Content-Type header, let httpx set it properly with multipart boundary
            _client.headers.pop('Content-Type', None)
            for add_file in add_files:
                r = await _client.post(
                    '/api/v1/files',
                    data={
                        'challenge_id': current_challenge['id'],
                        'type': 'challenge',
                    },
                    files={'file': (add_file['name'], add_file['data'])},
                )
                r.raise_for_status()
                logger.info(
                    f'Added file {add_file["name"]} to {challenge.challenge_id} in CTFd '
                    f'with ID {current_challenge["id"]}'
                )

    async def sync_challenge(
        self, challenge: KonaChallengeItem, attachment_path: Path | None, rendered_description: str
    ) -> None:
        challenge_dict = {
            'name': challenge.name,
            'category': challenge.category,
            'description': rendered_description,
            'attribution': render_template(self.global_config.templates.ctfd_attribution, challenge=challenge),
            'type': challenge.ctfd.type,
            'state': challenge.ctfd.state.value,
            'max_attempts': challenge.scoring.ctfd.max_attempts,
            'connection_info': challenge.ctfd.connection_info,
        }

        if challenge.ctfd.type == 'static':
            challenge_dict['value'] = challenge.scoring.initial_value
        else:
            # TODO(es3n1n): this is wrong, need to think how to properly distinguish dynamic challenges
            challenge_dict['initial'] = challenge.scoring.initial_value
            challenge_dict['decay'] = challenge.scoring.ctfd.decay
            challenge_dict['minimum'] = challenge.scoring.minimum_value
            challenge_dict['function'] = challenge.scoring.ctfd.decay_function

        try:
            existing_challenge = next(
                chal
                for chal in self.challenges_on_remote
                if chal['name'] == challenge.name and chal['category'] == challenge.category
            )
            existing_challenge = await self._load_full_challenge(existing_challenge['id'])
        except StopIteration:
            existing_challenge = None

        if existing_challenge:
            logger.info(f'Challenge {challenge.challenge_id} already exists in CTFd with ID {existing_challenge["id"]}')
            await self._update_challenge(challenge, attachment_path, challenge_dict, existing_challenge)
            return

        logger.info(f'Challenge {challenge.challenge_id} does not exist in CTFd, creating it')
        await self._create_challenge(challenge, attachment_path, challenge_dict)
