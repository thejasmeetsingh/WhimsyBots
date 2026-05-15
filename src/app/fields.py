import json

from django import forms
from django.db import models
from django.core.exceptions import ValidationError

from app.utils import encrypt, decrypt


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
        kwargs.setdefault("widget", forms.TextInput)
        return super().formfield(**kwargs)


class EncryptedJSONField(models.TextField):
    """Stores encrypted JSON. Returns dict on read, encrypts on write."""

    def _validate_json(self, value):
        if isinstance(value, (dict, list)):
            return  # already a valid Python object
        try:
            json.loads(value)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValidationError(f"Enter valid JSON. Error: {e}", code="invalid_json")

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        try:
            return json.loads(decrypt(value))
        except Exception:
            return value

    def to_python(self, value):
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
        if value is None:
            return value
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        try:
            decrypt(value)
            return value  # already encrypted
        except Exception:
            return encrypt(value)

    def validate(self, value, model_instance):
        """Model-level validation — fires on full_clean()."""

        super().validate(value, model_instance)
        if value is not None:
            self._validate_json(value)

    def formfield(self, **kwargs):
        """Form-level validation — fires on admin Save."""

        validate_json = self._validate_json

        class JSONFormField(forms.CharField):
            widget = forms.Textarea

            def validate(self, value):
                super().validate(value)
                if value:
                    validate_json(value)

        kwargs.setdefault("form_class", JSONFormField)
        return super().formfield(**kwargs)
