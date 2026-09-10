"""Lazy public redis-py construction with no retry or URL safety overrides."""

from ._config import _Config


def _options(config: _Config) -> dict:
    options = {
        "max_connections": 1,
        "health_check_interval": 0,
        "decode_responses": False,
        "protocol": 2,
        "retry_on_error": [],
        "socket_connect_timeout": 2,
    }
    if config.url.startswith("rediss://"):
        options.update(ssl_cert_reqs="required", ssl_check_hostname=True)
    return options


def _sync_client(config: _Config):
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    return redis.Redis.from_url(
        config.url,
        **_options(config),
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0, supported_errors=()),
    )


def _async_client(config: _Config):
    from redis.asyncio import Redis
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff

    return Redis.from_url(
        config.url,
        **_options(config),
        socket_timeout=None,
        retry=Retry(NoBackoff(), 0, supported_errors=()),
    )
