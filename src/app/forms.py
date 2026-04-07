import requests
from django import forms

from app.models import Ollama


class OllamaForm(forms.ModelForm):
    default_model = forms.ChoiceField(choices=[], required=True)

    class Meta:
        model = Ollama
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        super().__init__(*args, **kwargs)
        
        # TODO: Create a Ollama Client to fetch the available models from the Ollama server.
        endpoint = instance.endpoint if instance.endpoint else "http://localhost:11434"
        if endpoint[-1] == "/":
            endpoint = endpoint[:-1]
        
        endpoint = endpoint.replace("localhost", "host.docker.internal")
        
        response = requests.get(url=f"{endpoint}/api/tags")
        if response.status_code == 200:
            models = response.json().get("models", [])
            choices = list(map(lambda x: (x["name"], x["name"], models)))
            self.fields["default_model"].choices = choices
