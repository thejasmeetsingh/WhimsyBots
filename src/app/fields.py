import json

from django import forms
from django.db import models

from app.utils import decrypt, encrypt


class EncryptedCharField(models.TextField):
    """Stores encrypted string. Decrypts automatically on access."""

    def from_db_value(self, value, expression, connection):
        """Called when data is loaded FROM the database."""

        if value is None:
            return value
        try:
            return decrypt(value)
        except Exception:
            return value  # return as-is if decryption fails (e.g. legacy data)

    def to_python(self, value):
        """Called during form validation / deserialization."""

        if value is None:
            return value
        try:
            return decrypt(value)
        except Exception:
            return value

    def get_prep_value(self, value):
        """Called when writing TO the database."""

        if value is None:
            return value
        try:
            # Avoid double-encrypting if already encrypted
            decrypt(value)
            return value  # already encrypted
        except Exception:
            return encrypt(value)  # plaintext coming in → encrypt it

    def formfield(self, **kwargs):
        kwargs.update(
            {"widget": forms.TextInput}
        )  # Use the conventional widget similar to CharField
        return super().formfield(**kwargs)


class EncryptedJSONField(models.TextField):
    """Stores encrypted JSON. Returns dict on read, encrypts on write."""

    def from_db_value(self, value, expression, connection):
        """Called when data is loaded FROM the database."""

        if value is None:
            return value
        try:
            return json.loads(decrypt(value))
        except Exception:
            return value

    def to_python(self, value):
        """Called during form validation / deserialization."""

        if value is None:
            return value
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(decrypt(value))
        except Exception:
            try:
                return json.loads(value)
            except Exception:
                return value

    def get_prep_value(self, value):
        """Called when writing TO the database."""

        if value is None:
            return value
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        try:
            decrypt(value)
            return value  # already encrypted
        except Exception:
            return encrypt(value)
