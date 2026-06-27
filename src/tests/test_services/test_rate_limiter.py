"""Tests for `src/services/rate_limiter.py` (Tier 1 — pure logic, fakeredis).

RateLimiter does no I/O of its own — the Redis client is injected.
We pass the `fake_redis` fixture from `conftest.py` for full isolation.
"""

from __future__ import annotations


from services.rate_limiter import RateLimiter


# ──────────────────────────────────────────────
# acquire — basic behaviour
# ──────────────────────────────────────────────


def test_acquire_returns_true_for_first_call(fake_redis):
    limiter = RateLimiter(fake_redis, bot_token="bot-A")
    assert limiter.acquire() is True


def test_acquire_returns_false_for_second_call_in_same_window(fake_redis):
    limiter = RateLimiter(fake_redis, bot_token="bot-A")
    assert limiter.acquire() is True
    assert limiter.acquire() is False


def test_acquire_is_scoped_per_bot_token(fake_redis):
    # One bot hitting the limit must NOT throttle a different bot.
    limiter_a = RateLimiter(fake_redis, bot_token="bot-A")
    limiter_b = RateLimiter(fake_redis, bot_token="bot-B")

    assert limiter_a.acquire() is True
    assert limiter_a.acquire() is False  # bot-A exhausted

    # bot-B has its own counter.
    assert limiter_b.acquire() is True


# ──────────────────────────────────────────────
# acquire — TTL / window expiry
# ──────────────────────────────────────────────


def test_acquire_again_after_window_expires(fake_redis):
    # Manipulate the underlying Redis to simulate window expiry:
    # delete the counter and confirm the limiter re-allows the request.
    limiter = RateLimiter(fake_redis, bot_token="bot-A")
    assert limiter.acquire() is True
    assert limiter.acquire() is False

    # Simulate window sliding past by flushing the key.
    fake_redis.delete(limiter.key)
    assert limiter.acquire() is True


def test_acquire_sets_ttl_on_first_call(fake_redis):
    limiter = RateLimiter(fake_redis, bot_token="bot-ttl")
    limiter.acquire()
    ttl = fake_redis.ttl(limiter.key)
    # TTL should be > 0 and ≤ 1 (the configured window).
    assert 0 < ttl <= 1


def test_acquire_key_uses_prefix(fake_redis):
    limiter = RateLimiter(fake_redis, bot_token="my-token")
    limiter.acquire()
    # The key must follow the documented `tg_rate:{token}` pattern.
    assert limiter.key == "tg_rate:my-token"
    assert fake_redis.exists("tg_rate:my-token") == 1


# ──────────────────────────────────────────────
# Configuration constants
# ──────────────────────────────────────────────


def test_rate_limiter_constants_match_telegram_limit():
    # Telegram's documented limit is 1 message per second per chat.
    assert RateLimiter.WINDOW_SECONDS == 1
    assert RateLimiter.MAX_REQUESTS == 1


def test_rate_limiter_key_prefix_is_stable():
    # The key prefix is part of the Redis schema; don't change it
    # without a migration plan.
    assert RateLimiter.KEY_PREFIX == "tg_rate"
