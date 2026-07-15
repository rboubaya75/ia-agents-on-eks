import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from ia_application import ChatSessionCommandRepository, DocumentManagement
from ia_security import TokenVerifier


class ReadinessProbe(Protocol):
    async def is_ready(self) -> bool: ...


class StaticReadinessProbe:
    def __init__(self, ready: bool = True) -> None:
        self._ready = ready

    async def is_ready(self) -> bool:
        return self._ready


class CompositeReadinessProbe:
    def __init__(self, probes: Sequence[ReadinessProbe]) -> None:
        if not probes:
            msg = "at least one readiness probe is required"
            raise ValueError(msg)
        self._probes = tuple(probes)

    async def is_ready(self) -> bool:
        results = await asyncio.gather(
            *(probe.is_ready() for probe in self._probes),
            return_exceptions=True,
        )
        return all(result is True for result in results)


@dataclass(frozen=True, slots=True)
class ApiScopes:
    profile_read: str = "platform/profile.read"
    chat_read: str = "platform/chat.read"
    chat_write: str = "platform/chat.write"
    document_read: str = "platform/documents.read"
    document_write: str = "platform/documents.write"


@dataclass(frozen=True, slots=True)
class AppContainer:
    token_verifier: TokenVerifier
    chat_sessions: ChatSessionCommandRepository
    readiness: ReadinessProbe
    documents: DocumentManagement | None = None
    scopes: ApiScopes = ApiScopes()
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    new_id: Callable[[], str] = lambda: str(uuid4())
