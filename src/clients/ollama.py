"""
Ollama language model client.

Provides a high-level interface for interacting with Ollama, a local language model service.
Handles model listing, chat completions, and tool calling functionality.
"""

import ollama


class OllamaClient:
    """
    Client for interacting with Ollama language model service.

    Wraps the official ollama Python package to provide:
    - Model listing and enumeration
    - Chat completions with message history
    - Multi-turn conversations with tool calling support
    - Optional API key authentication
    - Docker-aware endpoint handling

    Attributes:
        _client: Internal ollama.Client instance

    Example:
        >>> client = OllamaClient(endpoint="http://localhost:11434")
        >>> models = client.list_models()
        >>> print(models)  # ['llama2', 'mistral', ...]

        >>> response = client.chat(
        ...     model='mistral',
        ...     messages=[{"role": "user", "content": "Hello"}],
        ...     options={"temperature": 0.7}
        ... )
        >>> print(response["message"])  # Assistant response
    """

    _client = None

    def __init__(self, endpoint: str = None, api_key: str = None):
        """
        Initialize Ollama client.

        Args:
            endpoint (str | None): Ollama service URL
                Default: "http://localhost:11434"
                Automatically handles Docker localhost → host.docker.internal conversion
            api_key (str | None): Optional API key for authentication
                If provided, adds "Authorization: Bearer <api_key>" header

        Example:
            >>> # Local Ollama instance without authentication
            >>> client = OllamaClient()

            >>> # Remote Ollama with authentication
            >>> client = OllamaClient(
            ...     endpoint="https://api.example.com/ollama",
            ...     api_key="secret-key-123"
            ... )
        """

        _endpoint = self._get_clean_endpoint(endpoint or "http://localhost:11434")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = ollama.Client(host=_endpoint, headers=headers)

    @staticmethod
    def _get_clean_endpoint(endpoint: str) -> str:
        """
        Clean and normalize the Ollama endpoint URL.

        Handles Docker-specific conversions:
        - Removes trailing slashes for consistency
        - Converts "localhost" to "host.docker.internal" for Docker containers

        Args:
            endpoint (str): Raw endpoint URL

        Returns:
            str: Normalized endpoint URL

        Example:
            >>> OllamaClient._get_clean_endpoint("http://localhost:11434/")
            "http://host.docker.internal:11434"

            >>> OllamaClient._get_clean_endpoint("https://api.example.com/")
            "https://api.example.com"
        """

        endpoint = endpoint.strip("/").replace("localhost", "host.docker.internal")
        return endpoint

    def list_models(self) -> list[str]:
        """
        List all available models on the Ollama instance.

        Returns:
            list[str]: List of model names available

        Raises:
            ollama.ResponseError: If Ollama API call fails

        Example:
            >>> client = OllamaClient()
            >>> models = client.list_models()
            >>> print(models)
            ['llama2', 'mistral', 'neural-chat', 'dolphin-mixtral']
        """

        response = self._client.list()
        models = list(map(lambda x: x.model, response.models))
        return models

    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] = None,
        format: dict = None,
        options: dict = None,
    ) -> dict:
        """
        Send a chat message to Ollama and get a response.

        Supports multi-turn conversations with optional tool calling and structured output.

        Args:
            model (str): Model name to use (e.g., 'mistral', 'llama2')
            messages (list[dict]): Chat history in Ollama format
                Each message: {"role": "user|assistant|system", "content": "..."}
            tools (list[dict] | None): Available tools for function calling
                Format: Tool definitions in OpenAI function calling format
            format (dict | None): Output format specification
                For structured JSON responses, e.g., {"type": "string", "properties": {...}}
            options (dict | None): Model-specific options
                Common options:
                - "temperature" (float): 0.0-1.0, controls randomness
                - "num_ctx" (int): Context window size in tokens
                - "num_predict" (int): Max tokens to generate

        Returns:
            dict: Response with keys:
                - "message" (str): Assistant's text response
                - "tools" (list[dict] | None): Tool calls made (if any)
                - "ollama_ms" (int | None): total time taken by ollama service

        Raises:
            ollama.ResponseError: If Ollama API call fails

        Example - Simple chat:
            >>> response = client.chat(
            ...     model='mistral',
            ...     messages=[{"role": "user", "content": "What is 2+2?"}],
            ... )
            >>> print(response["message"])
            # "2 + 2 = 4"

        Example - Tool calling:
            >>> response = client.chat(
            ...     model='mistral',
            ...     messages=[{"role": "user", "content": "Get weather"}],
            ...     tools=[
            ...         {
            ...             "type": "function",
            ...             "function": {
            ...                 "name": "get_weather",
            ...                 "description": "Get weather for a city",
            ...                 "parameters": {"type": "object", "properties": {...}}
            ...             }
            ...         }
            ...     ]
            ... )
            >>> if response["tools"]:
            ...     print(f"Called tool: {response['tools'][0]['name']}")

        Example - Structured output:
            >>> response = client.chat(
            ...     model='mistral',
            ...     messages=[{"role": "user", "content": "Extract intent"}],
            ...     format={"type": "string", "properties": {"intent": {"type": "string"}}}
            ... )
        """

        response = self._client.chat(
            model=model, messages=messages, tools=tools, format=format, options=options
        )
        message = response.message
        total_duration_ns = response.total_duration
        ollama_ms = round(total_duration_ns / 1_000_000) if total_duration_ns else None

        tools = None

        # Extract tool calls if any were made
        if message.tool_calls:
            tools = list(map(lambda x: x["function"], message.tool_calls))

        result = {
            "message": message.content.strip(),
            "tools": tools,
            "ollama_ms": ollama_ms,
        }

        return result
