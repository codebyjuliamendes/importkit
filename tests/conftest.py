from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from importkit import migrate
from importkit.app import create_app
from importkit.config import Settings


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'test.sqlite'}"
    migrate.upgrade(url)
    return url


@pytest.fixture
def make_settings(database_url: str) -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        base: dict[str, Any] = {
            "database_url": database_url,
            "environment": "test",
            "allow_test_email_domains": True,
            "rate_limit_writes": 0,
            "log_format": "console",
            "log_level": "WARNING",
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[call-arg]

    return factory


@pytest.fixture
def make_client(make_settings: Callable[..., Settings]) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        client = TestClient(create_app(make_settings(**overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


def preview(client: TestClient, csv: str, **extra: Any) -> Any:
    response = client.post("/api/imports/preview", json={"csv": csv, **extra})
    assert response.status_code == 201, response.text
    return response.json()
