import uuid

from django.db import models
from django.core.validators import URLValidator


class BaseModel(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, unique=True, db_index=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Ollama(BaseModel):
    endpoint = models.URLField(default="http://localhost:11434", validators=[URLValidator(schemes=["http", "https"])])
    default_model = models.CharField(max_length=50)
    temperature = models.FloatField(default=0.7, help_text="Controls randomness in generation (higher = more random)")
    num_ctx = models.PositiveIntegerField(default=4096, help_text="Context length size (number of tokens)")
    num_predict = models.IntegerField(default=-1, help_text="Maximum number of tokens to generate (-1 = infinite)")

    class Meta:
        verbose_name = "Ollama"
        verbose_name_plural = "Ollama"
