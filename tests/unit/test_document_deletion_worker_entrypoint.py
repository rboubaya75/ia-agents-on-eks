import asyncio
from typing import cast

import pytest
from ia_backend_api import document_deletion_worker
from ia_backend_api.main import DocumentRuntime
from ia_backend_api.settings import BackendSettings


class FakeDeletionWorker:
    def __init__(self) -> None:
        self.wait_seconds: list[int] = []

    async def run_once(self, *, wait_seconds: int = 20) -> bool:
        self.wait_seconds.append(wait_seconds)
        raise asyncio.CancelledError


class FakeSettings:
    pass


def _settings() -> BackendSettings:
    return cast(BackendSettings, FakeSettings())


@pytest.mark.asyncio
async def test_deletion_worker_requires_document_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_deletion_worker, "BackendSettings", _settings)
    monkeypatch.setattr(
        document_deletion_worker,
        "create_document_runtime",
        lambda settings: None,
    )

    with pytest.raises(RuntimeError, match="requires"):
        await document_deletion_worker.run_worker()


@pytest.mark.asyncio
async def test_deletion_worker_long_polls_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = FakeDeletionWorker()
    runtime = cast(
        DocumentRuntime,
        type("Runtime", (), {"deletion_worker": worker})(),
    )
    monkeypatch.setattr(document_deletion_worker, "BackendSettings", _settings)
    monkeypatch.setattr(
        document_deletion_worker,
        "create_document_runtime",
        lambda settings: runtime,
    )

    with pytest.raises(asyncio.CancelledError):
        await document_deletion_worker.run_worker()

    assert worker.wait_seconds == [20]
