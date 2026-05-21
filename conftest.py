"""Pytest configuration shared across all tests."""
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e: end-to-end tests requiring live services (LLM, Postgres, Redis)",
    )
