from typing import Annotated, Protocol, runtime_checkable

from ia_domain import Classification, Role, TenantId, UserId
from pydantic import BaseModel, ConfigDict, Field


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    user_id: Annotated[UserId, Field(min_length=1, max_length=128)]
    tenant_id: Annotated[TenantId, Field(min_length=1, max_length=128)]
    email: Annotated[str, Field(min_length=3, max_length=320)]
    roles: Annotated[frozenset[Role], Field(min_length=1)]
    scopes: frozenset[str] = Field(default_factory=frozenset)
    maximum_classification: Classification
    token_id: Annotated[str, Field(min_length=1, max_length=256)] | None = None


@runtime_checkable
class TokenVerifier(Protocol):
    async def verify(self, access_token: str) -> Principal: ...
