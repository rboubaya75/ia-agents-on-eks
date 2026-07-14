from typing import cast

import pytest
from ia_backend_api import document_worker
from ia_backend_api.main import DocumentRuntime
from ia_backend_api.settings import BackendSettings


class StopWorkerError(RuntimeError):
    pass


class FakeWorker:
    def __init__(self) -> None:
        self.wait_seconds: list[int] = []

    async def run_once(self, *, wait_seconds: int = 20) -> bool:
        self.wait_seconds.append(wait_seconds)
        raise StopWorkerError


def _settings() -> BackendSettings:
    return cast(BackendSettings, object())


@pytest.mark.asyncio
async def test_worker_requires_document_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_worker, "BackendSettings", _settings)
    monkeypatch.setattr(document_worker, "create_document_runtime", lambda settings: None)

    with pytest.raises(RuntimeError, match="requires"):
        await document_worker.run_worker()


@pytest.mark.asyncio
async def test_worker_long_polls_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = FakeWorker()
    runtime = cast(
        DocumentRuntime,
        type("Runtime", (), {"worker": worker})(),
    )
    monkeypatch.setattr(document_worker, "BackendSettings", _settings)
    monkeypatch.setattr(
        document_worker,
        "create_document_runtime",
        lambda settings: runtime,
    )

    with pytest.raises(StopWorkerError):
        await document_worker.run_worker()

    assert worker.wait_seconds == [20]
