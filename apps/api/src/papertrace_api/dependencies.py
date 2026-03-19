from __future__ import annotations

from functools import lru_cache

from papertrace_core.settings import Settings, get_settings


@lru_cache(maxsize=1)
def get_app_settings() -> Settings:
    return get_settings()
