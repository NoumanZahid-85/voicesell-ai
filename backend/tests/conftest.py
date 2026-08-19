"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os

# Set test environment variables BEFORE importing app modules
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("DEBUG", "false")
