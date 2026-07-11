from typing import Never

from ia_application import ChatSessionCommandRepository
from ia_domain import ChatSession, Role, SessionId
from ia_security import Principal

from ia_backend_api.errors import ApiError

_READ_PRIVILEGED_ROLES = frozenset({Role.SUPPORT, Role.TENANT_ADMIN, Role.PLATFORM_ADMIN})
_DELETE_PRIVILEGED_ROLES = frozenset({Role.TENANT_ADMIN, Role.PLATFORM_ADMIN})


class ChatSessionService:
    def __init__(self, repository: ChatSessionCommandRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        principal: Principal,
        session_id: SessionId,
        title: str,
        now: object,
    ) -> ChatSession:
        from datetime import datetime

        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            msg = "now must be a timezone-aware datetime"
            raise ValueError(msg)
        session = ChatSession(
            tenant_id=principal.tenant_id,
            session_id=session_id,
            user_id=principal.user_id,
            status="active",
            title=title,
            created_at=now,
            last_activity=now,
            message_count=0,
        )
        await self._repository.save(session)
        return session

    async def list_for_principal(self, principal: Principal) -> tuple[ChatSession, ...]:
        return await self._repository.list_for_user(principal.tenant_id, principal.user_id)

    async def get_for_principal(
        self,
        *,
        principal: Principal,
        session_id: SessionId,
    ) -> ChatSession:
        session = await self._repository.get(principal.tenant_id, session_id)
        if session is None or not self._can_read(principal, session):
            self._not_found()
        return session

    async def delete_for_principal(
        self,
        *,
        principal: Principal,
        session_id: SessionId,
    ) -> None:
        session = await self._repository.get(principal.tenant_id, session_id)
        if session is None or not self._can_delete(principal, session):
            self._not_found()
        deleted = await self._repository.delete(principal.tenant_id, session_id)
        if not deleted:
            self._not_found()

    @staticmethod
    def _can_read(principal: Principal, session: ChatSession) -> bool:
        return session.user_id == principal.user_id or not principal.roles.isdisjoint(
            _READ_PRIVILEGED_ROLES
        )

    @staticmethod
    def _can_delete(principal: Principal, session: ChatSession) -> bool:
        return session.user_id == principal.user_id or not principal.roles.isdisjoint(
            _DELETE_PRIVILEGED_ROLES
        )

    @staticmethod
    def _not_found() -> Never:
        raise ApiError(
            status_code=404,
            code="session_not_found",
            message="The chat session was not found.",
        )
