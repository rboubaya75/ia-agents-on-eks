import asyncio
import logging

from ia_backend_api.main import create_document_runtime
from ia_backend_api.settings import BackendSettings

_LOGGER = logging.getLogger(__name__)


async def run_worker() -> None:
    settings = BackendSettings()
    runtime = create_document_runtime(settings)
    if runtime is None:
        raise RuntimeError("document deletion worker requires IA_DOCUMENT_API_ENABLED=true")
    _LOGGER.info(
        "document_deletion_worker_started",
        extra={"event": "document_deletion_worker_started"},
    )
    while True:
        try:
            await runtime.deletion_worker.run_once(wait_seconds=20)
        except asyncio.CancelledError:
            _LOGGER.info(
                "document_deletion_worker_stopped",
                extra={"event": "document_deletion_worker_stopped", "reason": "cancelled"},
            )
            raise
        except Exception as error:
            _LOGGER.error(
                "document_deletion_worker_iteration_failed",
                extra={
                    "event": "document_deletion_worker_iteration_failed",
                    "error_type": type(error).__name__,
                },
            )
            await asyncio.sleep(1)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
