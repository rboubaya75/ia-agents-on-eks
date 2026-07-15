import asyncio
import logging
from typing import cast

import pytest
from ia_backend_api import document_worker
from ia_backend_api.main import DocumentRuntime
from ia_backend_api.settings import BackendSettings


class FakeWorker:
    def __init__(self) -> None:
        self.wait_seconds: list[int] = []
        self.timing: tuple[int, int, int] | None = None

    def configure_timing(
        self,
        *,
        lease_ttl_seconds: int,
        heartbeat_interval_seconds: int,
        visibility_timeout_seconds: int,
    ) -> None:
        self.timing = (
            lease_ttl_seconds,
            heartbeat_interval_seconds,
            visibility_timeout_seconds,
        )

    async def run_once(self, *, wait_seconds: int = 20) -> bool:
        self.wait_seconds.append(wait_seconds)
        raise asyncio.CancelledError


class FailingThenCancelledWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def run_once(self, *, wait_seconds: int = 20) -> bool:
        self.wait_seconds.append(wait_seconds)
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("sensitive provider detail")
        raise asyncio.CancelledError


class FakeSettings:
    document_ingestion_lease_ttl_seconds = 300
    document_ingestion_heartbeat_interval_seconds = 30
    document_queue_visibility_timeout_seconds = 600


def _settings() -> BackendSettings:
    return cast(BackendSettings, FakeSettings())


def _runtime(worker: FakeWorker) -> DocumentRuntime:
    return cast(
        DocumentRuntime,
        type("Runtime", (), {"worker": worker})(),
    )


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
    monkeypatch.setattr(document_worker, "BackendSettings", _settings)
    monkeypatch.setattr(
        document_worker,
        "create_document_runtime",
        lambda settings: _runtime(worker),
    )

    with pytest.raises(asyncio.CancelledError):
        await document_worker.run_worker()

    assert worker.timing == (300, 30, 600)
    assert worker.wait_seconds == [20]


@pytest.mark.asyncio
async def test_worker_logs_safe_iteration_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = FailingThenCancelledWorker()

    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr(document_worker, "BackendSettings", _settings)
    monkeypatch.setattr(
        document_worker,
        "create_document_runtime",
        lambda settings: _runtime(worker),
    )
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    caplog.set_level(logging.ERROR, logger=document_worker.__name__)

    with pytest.raises(asyncio.CancelledError):
        await document_worker.run_worker()

    failure_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "document_worker_iteration_failed"
    ]
    assert len(failure_records) == 1
    assert getattr(failure_records[0], "error_type", None) == "RuntimeError"
    assert "sensitive provider detail" not in caplog.text
