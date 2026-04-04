"""CLI entry point for the event engine."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import structlog

from event_engine.config import Settings


def _configure_logging(verbose: bool) -> None:
    log_level = "DEBUG" if verbose else "INFO"
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


async def _cmd_run(args: argparse.Namespace, settings: Settings) -> None:
    """Run the full scrape pipeline (existing behavior)."""
    from event_engine.orchestrator import run

    logger = structlog.get_logger()
    logger.info(
        "engine_starting",
        dry_run=args.dry_run,
        source_filter=args.source_filter,
        sources_dir=str(settings.sources_dir),
    )

    report = await run(
        database_url=settings.database_url,
        sources_dir=str(settings.sources_dir),
        source_filter=args.source_filter,
        dry_run=args.dry_run,
        max_concurrency=settings.max_concurrency,
        anthropic_api_key=settings.anthropic_api_key or None,
    )

    print()
    print(report.to_markdown())

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(report.to_markdown())
            f.write("\n")

    if report.sources_failed > 0 and report.sources_succeeded == 0:
        sys.exit(1)


async def _cmd_audit_source(args: argparse.Namespace, settings: Settings) -> None:
    """Run graduation audit for a single source."""
    from urllib.parse import urlparse

    import yaml

    from event_engine.classify.ai_classifier import AIClassifier
    from event_engine.db.connection import create_pool
    from event_engine.models import load_sources
    from event_engine.models.source_config import SourceFile
    from event_engine.spot_check.auditor import audit_source

    log = structlog.get_logger()

    # Resolve source_id -> domain (include disabled sources)
    source = None
    for yml_path in sorted(settings.sources_dir.glob("*.yml")):
        with open(yml_path) as f:
            data = yaml.safe_load(f)
        if not data or "sources" not in data:
            continue
        sf = SourceFile.model_validate(data)
        source = next((s for s in sf.sources if s.id == args.source_id), None)
        if source:
            break

    if source is None:
        log.error("source_not_found", source_id=args.source_id)
        sys.exit(1)

    domain = urlparse(source.base_url).netloc
    log.info("audit_starting", source_id=args.source_id, domain=domain)

    pool = await create_pool(settings.database_url)
    try:
        classifier = AIClassifier(api_key=settings.anthropic_api_key)
        result = await audit_source(
            pool=pool,
            classifier=classifier,
            source_id=args.source_id,
            domain=domain,
        )
    finally:
        await pool.close()

    if result.graduated:
        log.info(
            "audit_result_graduated",
            source_id=args.source_id,
            passed=result.passed,
            total=result.total,
            events_activated=result.events_activated,
        )
    else:
        log.warning(
            "audit_result_not_graduated",
            source_id=args.source_id,
            passed=result.passed,
            total=result.total,
        )
        sys.exit(2)


async def _cmd_spot_check(args: argparse.Namespace, settings: Settings) -> None:
    """Run the weekly random spot-check across all verified sources."""
    from event_engine.classify.ai_classifier import AIClassifier
    from event_engine.db.connection import create_pool
    from event_engine.spot_check.spot_checker import run_spot_check

    log = structlog.get_logger()

    pool = await create_pool(settings.database_url)
    try:
        classifier = AIClassifier(api_key=settings.anthropic_api_key)
        result = await run_spot_check(
            pool=pool,
            classifier=classifier,
            sources_dir=settings.sources_dir,
        )
    finally:
        await pool.close()

    log.info(
        "spot_check_summary",
        sources_checked=result.sources_checked,
        sources_downgraded=result.sources_downgraded,
    )

    if result.sources_downgraded > 0:
        sys.exit(3)


def main() -> None:
    """Main entry point — parse args and dispatch to subcommand."""
    parser = argparse.ArgumentParser(
        prog="event-engine",
        description="MyKidSpots Event Engine",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command")

    # run subcommand (default)
    run_parser = subparsers.add_parser("run", help="Run the full scrape pipeline (default)")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--source-filter", type=str, default=None)

    # audit-source subcommand
    audit_parser = subparsers.add_parser("audit-source", help="Run graduation audit for a source")
    audit_parser.add_argument("source_id", type=str, help="Source ID to audit (e.g. visitraleigh-events)")

    # spot-check subcommand
    subparsers.add_parser("spot-check", help="Run weekly random spot-check on verified sources")

    args = parser.parse_args()

    # Default to 'run' if no subcommand given (backwards compatibility)
    if args.command is None:
        args.command = "run"
        args.dry_run = False
        args.source_filter = None

    _configure_logging(args.verbose)
    logger = structlog.get_logger()

    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as e:
        logger.error("config_error", error=str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    dispatch = {
        "run": _cmd_run,
        "audit-source": _cmd_audit_source,
        "spot-check": _cmd_spot_check,
    }

    asyncio.run(dispatch[args.command](args, settings))


if __name__ == "__main__":
    main()
