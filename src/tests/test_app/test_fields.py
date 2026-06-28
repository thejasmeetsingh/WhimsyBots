"""Tests for 'src/app/fields.py'.

We exercise the three hooks on each field class directly with raw
strings / dicts rather than spinning up an ORM model. The DB-backed
"round trip" path is covered indirectly: encrypt → set → get → decrypt
matches what 'from_db_value' and 'to_python' are responsible for.
"""

from __future__ import annotations

import json

from app.fields import EncryptedCharField, EncryptedJSONField
from utils.crypto import encrypt

# ──────────────────────────────────────────────
# EncryptedCharField — from_db_value / to_python
# ──────────────────────────────────────────────


def test_encrypted_char_field_from_db_value_returns_plaintext():
    plaintext = "super-secret-token"
    field = EncryptedCharField()
    assert field.from_db_value(encrypt(plaintext), None, None) == plaintext


def test_encrypted_char_field_to_python_returns_plaintext():
    plaintext = "another-secret"
    field = EncryptedCharField()
    assert field.to_python(encrypt(plaintext)) == plaintext


def test_encrypted_char_field_from_db_value_none_returns_none():
    field = EncryptedCharField()
    assert field.from_db_value(None, None, None) is None


def test_encrypted_char_field_to_python_none_returns_none():
    field = EncryptedCharField()
    assert field.to_python(None) is None


def test_encrypted_char_field_from_db_value_passthrough_on_bad_ciphertext():
    # Legacy data may not be Fernet ciphertext; the field must NOT crash
    # — it should return the raw value so the admin can still see/edit it.
    field = EncryptedCharField()
    bad_value = "not-a-real-fernet-token"
    assert field.from_db_value(bad_value, None, None) == bad_value


def test_encrypted_char_field_to_python_passthrough_on_bad_ciphertext():
    field = EncryptedCharField()
    bad_value = "still-not-encrypted"
    assert field.to_python(bad_value) == bad_value


# ──────────────────────────────────────────────
# EncryptedCharField — get_prep_value (write path)
# ──────────────────────────────────────────────


def test_encrypted_char_field_get_prep_value_encrypts_plaintext():
    plaintext = "write-me-to-db"
    field = EncryptedCharField()
    stored = field.get_prep_value(plaintext)
    # Stored value should be ciphertext, not the plaintext.
    assert stored != plaintext
    assert isinstance(stored, str)


def test_encrypted_char_field_get_prep_value_does_not_double_encrypt():
    # If the caller hands us a value that's already encrypted, the
    # field should detect that and pass it through unchanged.
    ciphertext = encrypt("already-encrypted")
    field = EncryptedCharField()
    stored = field.get_prep_value(ciphertext)
    assert stored == ciphertext


def test_encrypted_char_field_get_prep_value_none_returns_none():
    field = EncryptedCharField()
    assert field.get_prep_value(None) is None


# ──────────────────────────────────────────────
# EncryptedJSONField — from_db_value / to_python
# ──────────────────────────────────────────────


def test_encrypted_json_field_from_db_value_returns_dict():
    payload = {"key": "value", "nested": {"a": 1}}
    ciphertext = encrypt(json.dumps(payload))
    field = EncryptedJSONField()
    assert field.from_db_value(ciphertext, None, None) == payload


def test_encrypted_json_field_to_python_returns_dict():
    payload = {"foo": "bar"}
    ciphertext = encrypt(json.dumps(payload))
    field = EncryptedJSONField()
    assert field.to_python(ciphertext) == payload


def test_encrypted_json_field_to_python_passes_through_native_dict():
    # When called during deserialization with an already-decoded dict,
    # the field should NOT re-encrypt/re-decrypt — just return it as-is.
    payload = {"already": "decoded"}
    field = EncryptedJSONField()
    assert field.to_python(payload) == payload


def test_encrypted_json_field_to_python_passes_through_native_list():
    payload = [1, 2, 3]
    field = EncryptedJSONField()
    assert field.to_python(payload) == payload


def test_encrypted_json_field_from_db_value_none_returns_none():
    field = EncryptedJSONField()
    assert field.from_db_value(None, None, None) is None


def test_encrypted_json_field_to_python_none_returns_none():
    field = EncryptedJSONField()
    assert field.to_python(None) is None


def test_encrypted_json_field_from_db_value_passthrough_on_bad_ciphertext():
    # Legacy / corrupt rows should fall through to the raw value.
    field = EncryptedJSONField()
    assert field.from_db_value("not-encrypted", None, None) == "not-encrypted"


def test_encrypted_json_field_to_python_decodes_plain_json_when_decrypt_fails():
    # If decryption fails (legacy plaintext JSON row), attempt a plain
    # JSON decode before falling through to the raw string.
    field = EncryptedJSONField()
    raw_json = json.dumps({"legacy": True})
    assert field.to_python(raw_json) == {"legacy": True}


def test_encrypted_json_field_to_python_returns_raw_when_all_decoding_fails():
    field = EncryptedJSONField()
    assert field.to_python("not-encrypted-and-not-json") == "not-encrypted-and-not-json"


# ──────────────────────────────────────────────
# EncryptedJSONField — get_prep_value
# ──────────────────────────────────────────────


def test_encrypted_json_field_get_prep_value_encrypts_dict():
    payload = {"a": 1, "b": [1, 2, 3]}
    field = EncryptedJSONField()
    stored = field.get_prep_value(payload)
    # Stored value is a Fernet ciphertext string — not a JSON literal.
    assert stored != json.dumps(payload)
    assert isinstance(stored, str)


def test_encrypted_json_field_get_prep_value_encrypts_list():
    payload = [1, 2, {"x": "y"}]
    field = EncryptedJSONField()
    stored = field.get_prep_value(payload)
    assert isinstance(stored, str)
    assert stored != json.dumps(payload)


def test_encrypted_json_field_get_prep_value_does_not_double_encrypt():
    payload = {"already": "encrypted"}
    ciphertext = encrypt(json.dumps(payload))
    field = EncryptedJSONField()
    # Already-encrypted JSON must round-trip unchanged.
    assert field.get_prep_value(ciphertext) == ciphertext


def test_encrypted_json_field_get_prep_value_none_returns_none():
    field = EncryptedJSONField()
    assert field.get_prep_value(None) is None
