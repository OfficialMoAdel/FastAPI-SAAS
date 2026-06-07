"""Entry point for running the ARQ background worker with optimal settings."""

import asyncio
import sys

from arq.cli import cli

from src.app.core.logger import get_logger

logger = get_logger(__name__)


def main() -> None:
    """Initialize event loop policy and run the ARQ worker."""
    # Set uvloop policy on Unix platforms for better performance
    if sys.platform != "win32":
        try:
            import uvloop

            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            logger.info("Configured uvloop as the event loop policy for the worker")
        except ImportError:
            logger.warning("uvloop not installed, falling back to default asyncio event loop")

    # Execute ARQ CLI
    # Provide the default settings path if no arguments are provided
    if len(sys.argv) == 1:
        sys.argv.append("src.app.core.worker.settings.WorkerSettings")

    cli()


if __name__ == "__main__":
    main()
