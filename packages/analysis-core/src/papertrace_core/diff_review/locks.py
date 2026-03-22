from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

from papertrace_core.settings import Settings


@contextmanager
def review_build_lock(cache_key: str, settings: Settings) -> Iterator[bool]:
    client: Redis | None = None
    token = str(uuid4())
    lock_key = f"review-build:{cache_key}"
    try:
        client = Redis.from_url(settings.redis_url)
        acquired = bool(client.set(lock_key, token, nx=True, ex=900))
    except RedisError:
        acquired = True
        client = None
    try:
        yield acquired
    finally:
        if acquired and client is not None:
            try:
                current = cast(bytes | str | None, client.get(lock_key))
                current_value = current.decode("utf-8") if isinstance(current, bytes) else current
                if current_value == token:
                    client.delete(lock_key)
            except RedisError:
                pass
