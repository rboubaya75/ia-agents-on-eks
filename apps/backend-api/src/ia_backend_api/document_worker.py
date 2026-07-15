import asyncio
import logging

from ia_backend_api.main import create_document_runtime
from ia_backend_api.settings import BackendSettings

_LOGGER = logging.getLogger(__name__)


async def run_worker() -> None:
    settings = BackendSettings()
    runtime = create_document_runtime(settings)
    if runtime is None:
        raise RuntimeError("document worker requires IA_DOCUMENT_API_ENABLED=true")
    runtime.worker.configure_timing(
        lease_ttl_seconds=settings.document_ingestion_lease_ttl_seconds,
        heartbeat_interval_seconds=settings.document_ingestion_heartbeat_interval_seconds,
        visibility_timeout_seconds=settings.document_queue_visibility_timeout_seconds,
    )
    _LOGGER.info(
        "document_worker_started",
        extra={
            "event": "document_worker_started",
            "lease_ttl_seconds": settings.document_ingestion_lease_ttl_seconds,
            "heartbeat_interval_seconds": (
                settings.document_ingestion_heartbeat_interval_seconds
            ),
            "visibility_timeout_seconds": settings.document_queue_visibility_timeout_seconds,
        },
    )
    while True:
        try:
            await runtime.worker.run_once(wait_seconds=20)
        except asyncio.CancelledError:
            _LOGGER.info(
                "document_worker_stopped",
                extra={"event": "document_worker_stopped", "reason": "cancelled"},
            )
            raise
        except Exception as error:
            # Do not log exception messages: provider errors can contain resource
            # details. The type and stable event name are sufficient for alerting.
            _LOGGER.error(
                "document_worker_iteration_failed",
                extra={
                    "event": "document_worker_iteration_failed",
                    "error_type": type(error).__name__,
                },
            )
            await asyncio.sleep(1)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
