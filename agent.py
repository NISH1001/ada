"""
Artifact-driven agent.

The agent knows nothing except what its artifacts tell it.
Swap the artifacts directory and you get a completely different agent.

Tools are generated at startup from artifact specs via a codegen agent,
validated in the Monty sandbox, and registered on an in-process FastMCP server.

Usage:
    uv run python agent.py "find python repos for building RAG pipelines"
    uv run python agent.py "search gitlab for kubernetes operators" --artifacts artifacts/code_search
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets.fastmcp import FastMCPToolset

from tool_server import build_tool_server


# ── Logging setup ──────────────────────────────────────────────────

logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{message}</cyan>",
)


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        level = (
            logger.level(record.levelname).name
            if record.levelname in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
            else record.levelno
        )
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)


# ── Artifact store ──────────────────────────────────────────────────


@dataclass
class ArtifactStore:
    """Loads all .md artifacts from a directory tree."""

    root: Path
    _artifacts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.root = Path(self.root)
        for md in sorted(self.root.rglob("*.md")):
            key = str(md.relative_to(self.root))
            self._artifacts[key] = md.read_text()
            logger.debug("[Store] indexed: {}", key)
        logger.info("[Store] ready — {} artifacts indexed from {}", len(self._artifacts), self.root)

    def get(self, key: str) -> str | None:
        return self._artifacts.get(key)

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._artifacts if k.startswith(prefix)]

    def root_index(self) -> str:
        return self._artifacts.get("index.md", "No root index found.")


# ── Agent factory ───────────────────────────────────────────────────


async def create_agent(artifact_dir: Path) -> tuple[Agent, ArtifactStore]:
    """Create an agent with dynamically generated tools from the artifact directory."""
    store = ArtifactStore(root=artifact_dir)

    toolsets = []
    mcp_server = await build_tool_server(artifact_dir)
    if mcp_server is not None:
        toolsets.append(FastMCPToolset(mcp_server))
        logger.info("[Agent] FastMCPToolset wired with {} tool(s)", len(mcp_server._tool_manager._tools))
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


# ── Main ────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Artifact-driven agent")
    parser.add_argument("query", help="The query to send to the agent")
    parser.add_argument(
        "--artifacts",
        default="artifacts/code_search",
        help="Path to artifacts directory (default: artifacts/code_search)",
    )
    args = parser.parse_args()

    artifact_dir = Path(args.artifacts)
    logger.info("Query: {}", args.query)

    agent, store = await create_agent(artifact_dir)

    result = await agent.run(args.query, deps=store)

    logger.info("Agent finished")
    print("\n" + result.output)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
