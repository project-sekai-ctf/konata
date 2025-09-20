import os
from time import sleep

import pymysql
from httpx import Client
from loguru import logger


CTFD_HOST = os.getenv('CTFD_HOST', 'ctfd')
MYSQL_HOST = os.getenv('MYSQL_HOST', 'mysql')
HTTP_OK = 200
HTTP_REDIRECT = 302


def main() -> None:
    nonce: str
    with Client(base_url=f'http://{CTFD_HOST}:8000', timeout=1) as client:
        while True:
            try:
                r = client.get('/', follow_redirects=True)
                if r.status_code in (HTTP_OK, HTTP_REDIRECT):
                    nonce = r.text.split("'csrfNonce': \"")[1].split('"')[0]
                    logger.info(f'Got nonce: {nonce}')
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning(f'CTFd is not up yet: {e}')
            sleep(2)

        r = client.post(
            '/setup',
            data={
                'ctf_name': 'kona',
                'ctf_description': 'kona',
                'user_mode': 'teams',
                'challenge_visibility': 'private',
                'account_visibility': 'public',
                'score_visibility': 'public',
                'registration_visibility': 'public',
                'verify_emails': 'false',
                'team_size': '',
                'name': 'admin',
                'email': 'admin@es3n1n.eu',
                'password': 'admin',
                'ctf_logo': '',
                'ctf_banner': '',
                'ctf_small_icon': '',
                'ctf_theme': 'core',
                'theme_color': '',
                'start': '79860',
                'end': '25466523060',
                'social_shares': 'true',
                '_submit': 'Finish',
                'nonce': nonce,
            },
            follow_redirects=True,
        )
        if r.status_code != HTTP_REDIRECT:
            r.raise_for_status()

    with pymysql.connect(host=MYSQL_HOST, user='ctfd', password='ctfd', database='ctfd') as conn:  # noqa: S106
        with conn.cursor() as cursor:
            # This way, access token will be persistent across all restarts
            cursor.execute(
                'INSERT INTO `tokens`(type, user_id, created, expiration, value, description) VALUES '
                "('user', 1, '2025-09-19 21:58:10.008657', '9999-01-01 00:00:00.000000', "
                "'ctfd_5bbb659f3ffb85533260c40af3fd46cd0ca4a742fe80fc7dac366e38c4675424', 'kona')",
            )
        conn.commit()

    logger.info('CTFd setup completed')


if __name__ == '__main__':
    main()
