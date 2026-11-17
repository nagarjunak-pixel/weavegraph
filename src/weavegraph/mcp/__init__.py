"""Drop-in MCP (Model Context Protocol) stdio server for WeaveGraph."""

from weavegraph.mcp.server import handle_jsonrpc_message, serve_stdio

__all__ = ["handle_jsonrpc_message", "serve_stdio"]
