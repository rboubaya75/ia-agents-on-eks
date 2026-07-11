from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from ia_application import ChatSessionCommandRepository
from ia_security import TokenVerifier


class ReadinessProbe(Protocol):
    async def is_ready(self) -> bool: ...


class StaticReadinessProbe:
    def __init__(self, ready: bool = True) -> None:
        self._ready = ready

    async def is_ready(self) -> bool:
        return self._ready


@dataclass(frozen=True, slots=True)
class ApiScopes:
    profile_read: str = "platform/profile.read"
    chat_read: str = "platform/chat.read"
    chat_write: str = "platform/chat.write"


@dataclass(frozen=True, slots=True)
class AppContainer:
    token_verifier: TokenVerifier
    chat_sessions: ChatSessionCommandRepository
    readiness: ReadinessProbe
    scopes: ApiScopes = ApiScopes()
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    new_id: Callable[[], str] = lambda: str(uuid4())
