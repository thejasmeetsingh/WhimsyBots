import ollama


class OllamaClient:
    _client = None
    _model = None

    def __init__(self, endpoint=None, model=None):
        _endpoint = self._get_clean_endpoint(endpoint)
        self._client = ollama.Client(host=_endpoint)
        self._model = model

    @staticmethod
    def _get_clean_endpoint(endpoint: str):
        endpoint = endpoint.strip("/").replace("localhost", "host.docker.internal")
        return endpoint

    def list_models(self):
        response = self._client.list()
        models = list(map(lambda x: {"name": x.name, "model": x.model}, response.models))
        return models

    def chat(self, messages, tools, format, options):
        response = self._client.chat(
            model=self._model,
            messages=messages,
            tools=tools,
            format=format,
            options=options
        )
        message = response.message
        tools = None

        if message.tool_calls:
            tools = list(map(lambda x: x["function"], message.tool_calls))

        result = {
            "message": message.content,
            "tools": tools
        }

        return result
