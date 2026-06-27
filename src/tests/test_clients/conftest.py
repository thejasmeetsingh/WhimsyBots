"""Local conftest for `src/tests/test_clients/`.

The `mcp` SDK package is only available inside the project's Docker image.
Outside of Docker (local dev / CI workers without Docker) we install
lightweight stand-ins into `sys.modules` so `from mcp import ...` succeeds
and we can patch individual SDK symbols from each test.

Mirrors the `pgvector` shim in `whimsybots/settings/test.py`.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock


def _install_mcp_stub() -> None:
    if "mcp" in sys.modules:
        return

    # Top-level `mcp` package.
    mcp_pkg = ModuleType("mcp")

    class _ClientSession:
        """Placeholder; tests patch this symbol directly."""

        def __init__(self, *args, **kwargs):
            # Accept arbitrary args so accidental use at import site doesn't
            # blow up before a test gets a chance to patch the symbol.
            self._args = args
            self._kwargs = kwargs

    class _StdioServerParameters:
        """Placeholder; tests patch this symbol directly."""

        def __init__(self, *args, **kwargs):
            # Mirror the real MCP SDK signature: accepts arbitrary kwargs
            # (command, args, env).
            self._args = args
            self._kwargs = kwargs

    mcp_pkg.ClientSession = _ClientSession
    mcp_pkg.StdioServerParameters = _StdioServerParameters
    sys.modules["mcp"] = mcp_pkg

    # `mcp.client` and submodules.
    mcp_client_pkg = ModuleType("mcp.client")
    sys.modules["mcp.client"] = mcp_client_pkg

    mcp_stdio_mod = ModuleType("mcp.client.stdio")
    mcp_stdio_mod.stdio_client = MagicMock(name="stdio_client")
    sys.modules["mcp.client.stdio"] = mcp_stdio_mod

    mcp_http_mod = ModuleType("mcp.client.streamable_http")
    mcp_http_mod.streamable_http_client = MagicMock(name="streamable_http_client")
    sys.modules["mcp.client.streamable_http"] = mcp_http_mod


_install_mcp_stub()
