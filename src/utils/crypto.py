"""
Symmetric encryption and token-hashing helpers.

The Fernet wrapper is used by 'app.fields.EncryptedCharField' and
'app.fields.EncryptedJSONField' to encrypt sensitive columns
(Telegram bot tokens, MCP server secrets) at rest. The token hash is a
deterministic SHA-256 digest stored alongside the encrypted token so we
can look up a bot by its token (via the webhook URL) without ever
needing to decrypt the stored ciphertext.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def get_fernet() -> Fernet:
    """
    Return a 'Fernet' instance keyed off Django's 'SECRET_KEY'.

    The secret key is SHA-256 hashed to derive 32 bytes of key material,
    then base64-url-encoded to match Fernet's expected key format.
    Calling this on every encrypt / decrypt is cheap enough (no DB or
    network I/O) that we don't bother caching the instance.

    Returns:
        A 'cryptography.fernet.Fernet' configured with the project-wide key.
    """

    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    encoded_key = base64.urlsafe_b64encode(key)  # Fernet expects base64
    return Fernet(encoded_key)


def encrypt(plaintext: str) -> str:
    """
    Encrypt 'plaintext' and return the ciphertext as a 'str'.

    Args:
        plaintext: The cleartext value to encrypt. Will be UTF-8 encoded.

    Returns:
        The Fernet-encrypted ciphertext, decoded as a 'str' so it can
        be stored directly in a text column.
    """

    fernet = get_fernet()
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """
    Decrypt a Fernet ciphertext back to its cleartext 'str'.

    Args:
        ciphertext: The Fernet-encrypted ciphertext (as returned by 'encrypt').

    Returns:
        The original plaintext.
    """

    fernet = get_fernet()
    return fernet.decrypt(ciphertext.encode()).decode()


def get_token_hash(token: str) -> str:
    """
    Return a deterministic SHA-256 hex digest of 'token'.

    Used to look up a bot by its Telegram token from webhook requests
    without ever touching the encrypted token at rest. SHA-256 is
    collision-resistant enough for this use case (we only need to
    distinguish handful of tokens, not resist a pre-image attack).

    Args:
        token: The plaintext token to hash.

    Returns:
        64-character lowercase hex digest.
    """

    return hashlib.sha256(token.encode()).hexdigest()
