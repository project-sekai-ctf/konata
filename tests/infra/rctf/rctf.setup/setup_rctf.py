import os
import time

import psycopg2
from httpx import Client
from loguru import logger


RCTF_DATABASE_PASSWORD = os.getenv('RCTF_DATABASE_PASSWORD', 'kona')
RCTF_REDIS_PASSWORD = os.getenv('RCTF_REDIS_PASSWORD', 'kona')
RCTF_DATABASE_HOST = os.getenv('RCTF_DATABASE_HOST', 'postgres')
RCTF_REDIS_HOST = os.getenv('RCTF_REDIS_HOST', 'redis')
RCTF_HOST = os.getenv('RCTF_HOST', 'rctf')
HTTP_OK = 200


def main() -> None:
    logger.info('Waiting for rCTF to get up')

    with Client(base_url=f'http://{RCTF_HOST}', timeout=1) as client:
        while True:
            try:
                response = client.get('/api/v1/leaderboard/now?limit=100&offset=0')
                if response.status_code == HTTP_OK:
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning(f'rCTF is not up yet: {e}')
            time.sleep(1)

        logger.info('rCTF is up')

        r = client.post(
            '/api/v1/auth/register',
            json={
                'name': 'admin',
                'email': 'admin@es3n1n.eu',
            },
        )
        if r.status_code != HTTP_OK:
            logger.error('Failed to register admin user')
            return

        logger.info('Created the user')

        conn = psycopg2.connect(
            f"dbname='kona' user='kona' host='{RCTF_DATABASE_HOST}' password='{RCTF_DATABASE_PASSWORD}'"
        )
        with conn.cursor() as cursor:
            # This way, our team token will be persistent across all restarts
            cursor.execute(
                'UPDATE users SET '
                "id = '70aba09d-37fe-44b1-b9d8-531efd3d5743', "
                'perms = 7, '
                "created_at = '2025-09-19 15:05:05.590029+00'"
            )
            conn.commit()


if __name__ == '__main__':
    main()
