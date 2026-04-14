"""Agent factory — creates the main artifact-driven agent."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger
from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

from ada.artifacts import ArtifactStore
from ada.server import build_tool_server
from ada.tool_builder import ToolBuilder

# ── Logging setup ──────────────────────────────────────────────────

logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{message}</cyan>",
)


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        level = (
            logger.level(record.levelname).name
            if record.levelname in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
            else record.levelno
        )
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)


# ── Agent factory ──────────────────────────────────────────────────


async def create_agent(
    artifact_dir: Path, builder: ToolBuilder | None = None
) -> tuple[Agent, ArtifactStore]:
    """Create an agent with dynamically generated tools from the artifact directory."""
    store = ArtifactStore(root=artifact_dir)

    toolsets = []
    mcp_server = await build_tool_server(artifact_dir, builder=builder)
    if mcp_server is not None:
        toolsets.append(FastMCPToolset(mcp_server))
        logger.info(
            "[Agent] FastMCPToolset wired with {} tool(s)",
            len(mcp_server._tool_manager._tools),
        )
    else:
        logger.warning("[Agent] no dynamic tools built — agent will have only load_artifact")

    agent = Agent(
        "openai:gpt-5-nano",
        deps_type=ArtifactStore,
        toolsets=toolsets,
    )

    @agent.system_prompt
    async def system_prompt(ctx: RunContext[ArtifactStore]) -> str:
        tree = "\n".join(f"- {k}" for k in ctx.deps.keys())
        prompt = (
            ctx.deps.root_index()
            + "\n\n---\n\n"
            "## Available artifacts\n"
            f"{tree}\n\n"
            "Use `load_artifact` to read any artifact. "
            "Start by loading relevant index files to understand each category, "
            "then load specific artifacts as needed. "
            "Let the artifacts guide your behavior, tools, and constraints."
        )
        logger.debug("[Artifact] system prompt assembled ({} chars)", len(prompt))
        return prompt

    @agent.tool
    async def load_artifact(ctx: RunContext[ArtifactStore], path: str) -> str:
        """Load an artifact's full content by its relative path.

        Args:
            path: Relative path within artifacts dir (e.g. 'contexts/repo_search_expert.md')
        """
        content = ctx.deps.get(path)
        if content is None:
            logger.warning("[ToolCall] load_artifact('{}') → NOT FOUND", path)
            return f"Not found: '{path}'."
        preview = content[:120].replace("\n", " ").strip()
        logger.info("[ToolCall] load_artifact('{}') → {} chars | {}", path, len(content), preview)
        return content

    return agent, store
