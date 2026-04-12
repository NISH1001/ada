"""
Artifact-driven agent.

The agent knows nothing except what its artifacts tell it.
Swap the artifacts directory and you get a completely different agent.

Usage:
    uv run python agent.py "find python repos for building RAG pipelines"
    uv run python agent.py "search gitlab for kubernetes operators"
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

import httpx
from pydantic_ai import Agent, RunContext


# ── Logging setup ──────────────────────────────────────────────────

# Remove loguru default handler, add one with DEBUG level
logger.remove()
logger.add(sys.stderr, level="DEBUG", format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{message}</cyan>")


# Intercept stdlib logging (pydantic-ai, httpx, etc.) → loguru
class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        level = logger.level(record.levelname).name if record.levelname in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[InterceptHandler()], level=logging.DEBUG, force=True)


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


# ── Agent ───────────────────────────────────────────────────────────


agent = Agent(
    "openai:gpt-5-nano",
    deps_type=ArtifactStore,
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


# ── Artifact tools (the framework) ─────────────────────────────────


# @agent.tool
# async def list_artifacts(ctx: RunContext[ArtifactStore], prefix: str = "") -> list[str]:
#     """List available artifact paths. Optionally filter by prefix (e.g. 'tools/').
#
#     Args:
#         prefix: Optional path prefix to filter results
#     """
#     keys = ctx.deps.keys(prefix)
#     logger.info("[ToolCall] list_artifacts(prefix='{}') → {}", prefix, keys)
#     return keys


@agent.tool
async def load_artifact(ctx: RunContext[ArtifactStore], path: str) -> str:
    """Load an artifact's full content by its relative path.

    Args:
        path: Relative path within artifacts dir (e.g. 'contexts/repo_search_expert.md')
    """
    content = ctx.deps.get(path)
    if content is None:
        logger.warning("[ToolCall] load_artifact('{}') → NOT FOUND", path)
        return f"Not found: '{path}'. Use list_artifacts to see available paths."
    preview = content[:120].replace("\n", " ").strip()
    logger.info("[ToolCall] load_artifact('{}') → {} chars | {}", path, len(content), preview)
    return content


# ── Domain tools (example: repo search) ────────────────────────────


@agent.tool
async def search_github(
    ctx: RunContext[ArtifactStore],
    query: str,
    sort: str = "stars",
    per_page: int = 5,
) -> str:
    """Search GitHub public repositories.

    Args:
        query: Search query (supports qualifiers like language:python stars:>100)
        sort: Sort by: stars, forks, updated
        per_page: Number of results (max 30)
    """
    logger.info("[ToolCall] search_github(query='{}', sort='{}', per_page={})", query, sort, per_page)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": sort, "order": "desc", "per_page": min(per_page, 30)},
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        resp.raise_for_status()
        data = resp.json()

    logger.debug("[ToolCall] search_github → {} total results", data.get("total_count", 0))

    results = []
    for repo in data.get("items", []):
        license_id = (repo.get("license") or {}).get("spdx_id", "none")
        results.append(
            f"- {repo['full_name']} ({repo['html_url']})\n"
            f"  stars: {repo['stargazers_count']} | updated: {repo['updated_at'][:10]} | "
            f"lang: {repo.get('language', 'n/a')} | license: {license_id}\n"
            f"  {repo.get('description', 'No description')}"
        )
    return f"Found {data['total_count']} total. Top {len(results)}:\n\n" + "\n\n".join(results)


@agent.tool
async def search_gitlab(
    ctx: RunContext[ArtifactStore],
    query: str,
    order_by: str = "last_activity_at",
    per_page: int = 5,
) -> str:
    """Search GitLab public repositories.

    Args:
        query: Search query string
        order_by: Sort by: name, created_at, updated_at, last_activity_at, similarity
        per_page: Number of results (max 20)
    """
    logger.info("[ToolCall] search_gitlab(query='{}', order_by='{}', per_page={})", query, order_by, per_page)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://gitlab.com/api/v4/projects",
            params={
                "search": query,
                "order_by": order_by,
                "sort": "desc",
                "per_page": min(per_page, 20),
                "visibility": "public",
            },
        )
        resp.raise_for_status()
        projects = resp.json()

    logger.debug("[ToolCall] search_gitlab → {} results", len(projects))

    results = []
    for p in projects:
        results.append(
            f"- {p['path_with_namespace']} ({p['web_url']})\n"
            f"  stars: {p.get('star_count', 0)} | updated: {p.get('last_activity_at', 'n/a')[:10]} | "
            f"forks: {p.get('forks_count', 0)}\n"
            f"  {p.get('description') or 'No description'}"
        )
    return f"Found {len(results)} results:\n\n" + "\n\n".join(results)


# ── Main ────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Artifact-driven agent")
    parser.add_argument("query", help="The query to send to the agent")
    parser.add_argument("--artifacts", default="artifacts", help="Path to artifacts directory (default: artifacts/)")
    args = parser.parse_args()

    artifact_dir = Path(args.artifacts)

    logger.info("Query: {}", args.query)

    store = ArtifactStore(root=artifact_dir)

    result = await agent.run(args.query, deps=store)

    logger.info("Agent finished")
    print("\n" + result.output)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
