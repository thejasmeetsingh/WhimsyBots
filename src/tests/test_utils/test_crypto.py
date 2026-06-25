"""Tests for `src/utils/crypto.py` (Tier 1 — pure logic)."""

from __future__ import annotations

import hashlib

import pytest
from cryptography.fernet import Fernet, InvalidToken

from utils.crypto import decrypt, encrypt, get_fernet, get_token_hash


# ──────────────────────────────────────────────
# get_fernet
# ──────────────────────────────────────────────


def test_get_fernet_returns_fernet_instance():
    f = get_fernet()
    assert isinstance(f, Fernet)


def test_get_fernet_is_deterministic_across_calls():
    # Same SECRET_KEY (test settings) ⇒ same derived key.
    # Verify by encrypting with one Fernet instance and decrypting
    # with another — this only works if the key material is identical.
    f1 = get_fernet()
    f2 = get_fernet()

    plaintext = "deterministic-key-check"
    ciphertext = f1.encrypt(plaintext.encode())
    assert f2.decrypt(ciphertext).decode() == plaintext


def test_get_fernet_can_decrypt_what_it_encrypted():
    plaintext = "round-trip-token"
    ciphertext = get_fernet().encrypt(plaintext.encode()).decode()
    assert get_fernet().decrypt(ciphertext.encode()).decode() == plaintext


# ──────────────────────────────────────────────
# encrypt / decrypt
# ──────────────────────────────────────────────


def test_encrypt_decrypt_round_trip():
    plaintext = "hello, secret world 🌍"
    ciphertext = encrypt(plaintext)

    # Fernet ciphertexts are url-safe-base64 and start with the
    # version byte (0x80) — they should never equal the plaintext.
    assert ciphertext != plaintext
    assert isinstance(ciphertext, str)

    assert decrypt(ciphertext) == plaintext


def test_encrypt_produces_different_ciphertexts_for_same_input():
    # Fernet uses a random IV under the hood, so encrypting the same
    # value twice must yield different ciphertexts.
    a = encrypt("same")
    b = encrypt("same")
    assert a != b


def test_encrypt_empty_string():
    # Edge case: empty plaintext should still encrypt and round-trip.
    ciphertext = encrypt("")
    assert decrypt(ciphertext) == ""


def test_decrypt_with_bad_ciphertext_raises():
    # A clearly invalid ciphertext (wrong length / non-Fernet payload)
    # must raise InvalidToken rather than silently returning garbage.
    with pytest.raises(InvalidToken):
        decrypt("not-a-valid-fernet-token")


def test_decrypt_ciphertext_from_other_fernet_raises():
    # Build a Fernet with an unrelated key and ensure its ciphertexts
    # cannot be decrypted by our project key.
    other = Fernet(Fernet.generate_key())
    foreign_ciphertext = other.encrypt(b"secret").decode()
    with pytest.raises(InvalidToken):
        decrypt(foreign_ciphertext)


# ──────────────────────────────────────────────
# get_token_hash
# ──────────────────────────────────────────────


def test_get_token_hash_is_deterministic():
    # Same input → same digest every call.
    assert get_token_hash("token-abc") == get_token_hash("token-abc")


def test_get_token_hash_is_64_char_lowercase_hex():
    digest = get_token_hash("any-token")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_get_token_hash_matches_sha256():
    # Sanity-check that the implementation matches a hand-rolled SHA-256.
    token = "telegram-bot-token:123456"
    expected = hashlib.sha256(token.encode()).hexdigest()
    assert get_token_hash(token) == expected


def test_get_token_hash_changes_with_input():
    # Even a one-character difference must produce a totally different digest.
    assert get_token_hash("a") != get_token_hash("b")
