import logging
import traceback

from django import forms

from app.models import Ollama, App
from app.ollama import OllamaClient


logger = logging.getLogger(__name__)


def get_model_field():
    return forms.ChoiceField(choices=[(None, "Select a model")], required=False, help_text="Please select model which supports tool calling")


class OllamaForm(forms.ModelForm):
    default_model = get_model_field()

    class Meta:
        model = Ollama
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        super().__init__(*args, **kwargs)

        try:
            endpoint = instance.endpoint if instance and instance.endpoint else None
            api_key = instance.api_key if instance and instance.api_key else None
            o_client = OllamaClient(endpoint=endpoint, api_key=api_key)

            models = o_client.list_models()
            choices = list(map(lambda model: (model, model), models))
            self.fields["default_model"].choices = choices
        except Exception as e:
            logger.error({
                "msg": "OllamaForm | Error while fetching models from Ollama",
                "error": str(e),
                "traceback": traceback.format_exc()
            })


class AppForm(forms.ModelForm):
    ollama_model = get_model_field()

    class Meta:
        model = App
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        ollama_obj = Ollama.objects.first()
        if ollama_obj:
            try:
                o_client = OllamaClient(endpoint=ollama_obj.endpoint, api_key=ollama_obj.api_key)
                models = o_client.list_models()
                choices = list(map(lambda model: (model, model), models))
                
                if ollama_obj.default_model:
                    choices.sort(key=lambda x: x[0] != ollama_obj.default_model)

                self.fields["ollama_model"].choices = choices
            except Exception as e:
                logger.error({
                    "msg": "AppForm | Error while fetching models from Ollama",
                    "error": str(e),
                    "traceback": traceback.format_exc()
                })
