"""Thin, LLM-free MCP tool surface for the searxng-rank search core.

One server, three stateless tools (``search`` / ``fetch`` / ``rerank``) that any
MCP host (OpenCode, Open WebUI, Claude, a raw vLLM + tool-calling model) can
drive. No LLM calls happen here -- the host's model owns the semantic loop and
this server only does deterministic retrieval.
"""

from src.mcp.server import main, mcp

__all__ = ["mcp", "main"]
