"""CLI entry point for the event engine."""

import argparse
import asyncio
import logging
import sys

import structlog

from event_engine.config import Settings
from event_engine.orchestrator import run


def main() -> None:
    """Main entry point — parse args and run the engine."""
    parser = argparse.ArgumentParser(
        prog="event-engine",
        description="MyKidSpots Event Engine — scrape and import library events",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and normalize but skip database writes",
    )
    parser.add_argument(
        "--source-filter",
        type=str,
        default=None,
        help="Glob pattern to filter source IDs (e.g., 'wake_*')",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Configure structured logging
    log_level = "DEBUG" if args.verbose else "INFO"
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level)
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )

    logger = structlog.get_logger()

    # Load settings
    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as e:
        logger.error("config_error", error=str(e))
        print(f"Error: {e}", file=sys.stderr)
        print("Hint: Set DATABASE_URL environment variable or create a .env file", file=sys.stderr)
        sys.exit(1)

    # Run the engine
    logger.info(
        "engine_starting",
        dry_run=args.dry_run,
        source_filter=args.source_filter,
        sources_dir=str(settings.sources_dir),
    )

    report = asyncio.run(
        run(
            database_url=settings.database_url,
            sources_dir=str(settings.sources_dir),
            source_filter=args.source_filter,
            dry_run=args.dry_run,
            max_concurrency=settings.max_concurrency,
            anthropic_api_key=settings.anthropic_api_key or None,
        )
    )

    # Print report
    print()
    print(report.to_markdown())

    # Write to GitHub Actions step summary if available
    import os

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(report.to_markdown())
            f.write("\n")

    # Exit with error if all sources failed
    if report.sources_failed > 0 and report.sources_succeeded == 0:
        sys.exit(1)
