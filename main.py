"""CLI entry point for Ada — artifact-driven agent."""

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from ada.agent import create_agent


async def main():
    parser = argparse.ArgumentParser(description="Ada — artifact-driven agent")
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
    asyncio.run(main())
