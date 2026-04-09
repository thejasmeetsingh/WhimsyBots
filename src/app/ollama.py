import ollama


class OllamaClient:
    _client = None

    def __init__(self, endpoint=None):
        _endpoint = self._get_clean_endpoint(endpoint or "http://localhost:11434")
        self._client = ollama.Client(host=_endpoint)

    @staticmethod
    def _get_clean_endpoint(endpoint: str):
        endpoint = endpoint.strip("/").replace("localhost", "host.docker.internal")
        return endpoint

    def list_models(self):
        response = self._client.list()
        models = list(map(lambda x: {"name": x.name, "model": x.model}, response.models))
        return models

    def chat(self, model, messages, tools=None, format=None, options=None):
        response = self._client.chat(
            model=model,
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
