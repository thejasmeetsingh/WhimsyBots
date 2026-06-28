"""Redis-backed sliding-window rate limiter for Telegram bots."""

from redis import Redis


class RateLimiter:
    """Redis-backed sliding window rate limiter.

    Uses Redis INCR + EXPIRE to count requests within a 1-second window.
    Scoped per bot_token so each bot has its own independent limit.

    Key pattern: tg_rate:{bot_token}
    TTL:         1 second (slides with each new window)

    Usage:
        limiter = RateLimiter(redis_client, bot_token)
        if limiter.acquire():
            # safe to send
        else:
            # throttled — wait and retry
    """

    KEY_PREFIX = "tg_rate"
    WINDOW_SECONDS = 1
    MAX_REQUESTS = 1  # Telegram limit: 1 msg/sec per chat

    def __init__(self, redis_client: Redis, bot_token: str):
        """Initialize the limiter with a Redis client and per-bot token.

        Args:
            redis_client: Redis instance used to back the counter.
            bot_token: Telegram bot token; scopes the counter key.
        """
        self.redis = redis_client
        self.key = f"{self.KEY_PREFIX}:{bot_token}"

    def acquire(self) -> bool:
        """Attempt to acquire a send slot within the current window.

        Uses a pipeline to atomically increment the counter and set TTL
        only on the first request in each window.

        Returns:
            True if the request is within the rate limit.
            False if the limit has been reached for this window.
        """
        pipe = self.redis.pipeline()
        pipe.incr(self.key)
        pipe.expire(self.key, self.WINDOW_SECONDS, nx=True)  # nx=True: only set if not exists
        count, _ = pipe.execute()

        return count <= self.MAX_REQUESTS
