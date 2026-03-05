from httpx import Response


def raise_for_status(response: Response) -> None:
    if response.is_success:
        return
    msg = f'{response.status_code} {response.reason_phrase} for url {response.url}: {response.text}'
    raise RuntimeError(msg)
