import logging
import traceback

from django import forms

from app.models import Ollama
from app.ollama import OllamaClient


logger = logging.getLogger(__name__)


class OllamaForm(forms.ModelForm):
    default_model = forms.ChoiceField(choices=[], required=False)

    class Meta:
        model = Ollama
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        super().__init__(*args, **kwargs)

        try:
            o_client = OllamaClient(endpoint=instance.endpoint if instance and instance.endpoint else None)
            models = o_client.list_models()
            choices = list(map(lambda x: (x["name"], x["name"], models)))
            self.fields["default_model"].choices = choices
        except Exception as e:
            logger.error({
                "msg": "Error while fetching models from Ollama",
                "error": str(e),
                "traceback": traceback.format_exc()
            })
