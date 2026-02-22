"""Database connection pool using asyncpg."""

import ssl

import asyncpg
import structlog

logger = structlog.get_logger()


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Create an asyncpg connection pool for Supabase.

    Supabase requires SSL for external connections. The pool is configured
    with reasonable defaults for a scraping workload.

    Args:
        database_url: PostgreSQL connection string.

    Returns:
        asyncpg.Pool ready for queries.
    """
    # Supabase requires SSL
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=10,
        ssl=ssl_ctx,
        command_timeout=30,
    )

    if pool is None:
        raise RuntimeError("Failed to create database connection pool")

    logger.info("database_pool_created", min_size=2, max_size=10)
    return pool
