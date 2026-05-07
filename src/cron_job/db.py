"""Database connection and session management for the Cron Job MCP server"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _build_db_url() -> str:
    """
    Construct the SQLAlchemy database URL from environment variables.

    Args:
        None

    Returns:
        str: Fully constructed connection string.

    Raises:
        EnvironmentError: If required DB credentials (NAME, USER, PASSWORD) are missing.
    """

    driver = os.getenv("DB_DRIVER", "postgresql+asyncpg")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "")
    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASSWORD", "")

    if not all([name, user, password]):
        raise EnvironmentError(
            "DB_NAME, DB_USER, and DB_PASSWORD environment variables are required."
        )

    return f"{driver}://{user}:{password}@{host}:{port}/{name}"


engine = create_async_engine(_build_db_url(), pool_pre_ping=True, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=engine, expire_on_commit=False, class_=AsyncSession
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Asynchronous context manager for database sessions.

    Provides a scoped session for database operations. Automatically
    handles session closure and rolls back transactions if a SQLAlchemyError
    occurs within the context.

    Yields:
        AsyncSessionLocal: An active SQLAlchemy async session.

    Raises:
        SQLAlchemyError: Re-raises any database errors encountered during the session.
    """

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except SQLAlchemyError:
            await session.rollback()
            raise
        finally:
            await session.close()
