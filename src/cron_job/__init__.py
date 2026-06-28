"""Cron Job package.

MCP server that lets the LLM create, list, update, and delete scheduled
jobs for a bot. Backed by a SQLAlchemy ORM model persisted in the main
PostgreSQL database. Entry point is 'cron_job.server'.
"""
