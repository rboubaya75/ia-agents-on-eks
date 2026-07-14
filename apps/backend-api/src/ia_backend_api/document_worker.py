import asyncio

from ia_backend_api.main import create_document_runtime
from ia_backend_api.settings import BackendSettings


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
    while True:
        try:
            await runtime.worker.run_once(wait_seconds=20)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
