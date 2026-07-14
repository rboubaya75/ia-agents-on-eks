import asyncio

from ia_backend_api.main import create_document_runtime
from ia_backend_api.settings import BackendSettings


async def run_worker() -> None:
    settings = BackendSettings()
    runtime = create_document_runtime(settings)
    if runtime is None:
        raise RuntimeError("document worker requires IA_DOCUMENT_API_ENABLED=true")
    while True:
        await runtime.worker.run_once(wait_seconds=20)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
