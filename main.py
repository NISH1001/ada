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
    parser.add_argument("query", help="The initial query to send to the agent")
    parser.add_argument(
        "--artifacts",
        default="artifacts/code_search",
        help="Path to artifacts directory (default: artifacts/code_search)",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Enable chat mode for multi-turn conversations",
    )
    args = parser.parse_args()

    artifact_dir = Path(args.artifacts)
    logger.info("Query: {}", args.query)

    agent, store = await create_agent(artifact_dir)

    result = await agent.run(args.query, deps=store)
    print("\n" + result.output)

    if args.chat:
        while True:
            try:
                user_input = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input or user_input.lower() in ("exit", "quit", "q"):
                break

            result = await agent.run(user_input, deps=store, message_history=result.all_messages())
            print("\n" + result.output)

    logger.info("Session ended")


if __name__ == "__main__":
    asyncio.run(main())
