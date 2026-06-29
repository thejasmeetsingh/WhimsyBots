"""Ollama language model client.

Provides a high-level interface for interacting with Ollama, a local language model service.
Handles model listing, chat completions, and tool calling functionality.
"""

from typing import Any, Optional

import ollama


class OllamaClient:
    """Client for interacting with Ollama language model service.

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

    def __init__(self, endpoint: str, api_key: Optional[str] = None):
        """Initialize Ollama client.

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
        """Clean and normalize the Ollama endpoint URL.

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
        """List all available models on the Ollama instance.

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
        models: list[str] = []

        for model in response.models:
            if model.model:
                models.append(model.model)

        return models

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        keep_alive: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Send a chat message to Ollama and get a response.

        Supports multi-turn conversations with optional tool calling and structured output.

        Args:
            model (str): Model name to use (e.g., 'mistral', 'llama2')
            messages (list[dict]): Chat history in Ollama format
                Each message: {"role": "user|assistant|system", "content": "..."}
            keep_alive (str | None): Model keep-alive duration
            tools (list[dict] | None): Available tools for function calling
                Format: Tool definitions in OpenAI function calling format
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
            ...     print(f"Called tool: {response['tools'][0]['function']['name']}")
        """
        if keep_alive:
            keep_alive = keep_alive.strip()
            keep_alive = int(keep_alive) if keep_alive in {"-1", "0"} else keep_alive

        response = self._client.chat(
            model=model,
            messages=messages,
            keep_alive=keep_alive,
            tools=tools,
            options=options,
        )
        message = response.message
        total_duration_ns = response.total_duration
        ollama_ms = round(total_duration_ns / 1_000_000) if total_duration_ns else None

        result: dict[str, Any] = {
            "message": message.content.strip(),
            "tools": message.tool_calls,
            "ollama_ms": ollama_ms,
        }

        return result

    def generate_embeddings(
        self,
        model: str,
        text: str,
        truncate: Optional[bool] = None,
        dimensions: Optional[int] = None,
    ) -> list[float]:
        """Generate embeddings for a given text using Ollama.

        Args:
            model (str): Model name to use for embeddings
                (e.g., 'nomic-embed-text', 'mxbai-embed-large')
            text (str): Text to generate embeddings for
            truncate (bool): truncate inputs that exceed the context window
            dimensions (int | None): Number of dimensions to generate embeddings for

        Returns:
            list[float]: List of embedding vectors (typically 768 or 1024 dimensions)

        Raises:
            ollama.ResponseError: If Ollama API call fails or model not found

        Example:
            >>> client = OllamaClient()
            >>> response = client.embed(
            ...     model='nomic-embed-text',
            ...     input='The quick brown fox jumps over the lazy dog'
            ... )
            >>> print(len(response.embeddings))  # 768 (for nomic-embed-text)
            >>> print(response.embeddings[:5])   # First 5 embedding values
        """
        response = self._client.embed(
            model=model,
            input=text,
            truncate=truncate,
            dimensions=dimensions,
        )

        # Ollama returns embeddings as a list of floats
        embeddings = response.embeddings

        if embeddings and isinstance(embeddings[0], list):
            embeddings = embeddings[0]

        return embeddings

    def fetch_model_capabilities(self, model: str) -> list[str]:
        """Fetch capabilities of a given model.

        Args:
            model (str): Model name to fetch details for (e.g., 'llama2', 'mistral')

        Returns:
            list[str]: capabilities

        Raises:
            ollama.ResponseError: If Ollama API call fails or model not found

        Example:
            >>> client = OllamaClient()
            >>> capabilities = client.fetch_model_capabilities('mistral')
            >>> print(capabilities)
            ["completion", "vision"]
        """
        response = self._client.show(model=model)

        # Convert response to dictionary
        return response.capabilities
