"""FastMCP server builder — registers generated tools for the main agent."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from mcp.server.fastmcp import FastMCP

from ada.tool_builder import ToolBuilder


async def build_tool_server(
    artifacts_root: Path, builder: ToolBuilder | None = None
) -> FastMCP | None:
    """Build a FastMCP server with tools generated from artifact specs."""
    builder = builder or ToolBuilder()

    tools = await builder.build_all(artifacts_root)
    if not tools:
        return None

    server = FastMCP("artifact-tools")
    for spec, fn in tools:
        server.add_tool(fn, name=spec.name, description=spec.description)
        logger.info("[ToolServer] registered: {}", spec.name)

    return server
