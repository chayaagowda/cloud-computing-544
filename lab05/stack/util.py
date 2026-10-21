"""Small helpers for working with AWS errors."""
import time
from contextlib import contextmanager

from botocore.exceptions import ClientError


def error_code(err: ClientError) -> str:
    return err.response.get("Error", {}).get("Code", "")


@contextmanager
def ignore_errors(*codes: str):
    """Ignore only expected AWS error codes; raise all other errors."""
    try:
        yield
    except ClientError as err:
        if error_code(err) not in codes:
            raise


def retry_on(codes, func, attempts=10, delay=15, sleep=time.sleep):
    """Retry only the specified AWS errors."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except ClientError as err:
            if error_code(err) not in codes or attempt == attempts:
                raise
            sleep(delay)
