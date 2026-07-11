import pytest
from ia_domain import Classification, Role, TenantId, UserId
from ia_security import Principal
from pydantic import ValidationError


def test_principal_is_strict_and_tenant_is_required() -> None:
    principal = Principal(
        user_id=UserId("user-1"),
        tenant_id=TenantId("tenant-a"),
        email="user@example.com",
        roles=frozenset({Role.USER}),
        scopes=frozenset({"chat:write"}),
        maximum_classification=Classification.INTERNAL,
    )
    assert principal.tenant_id == TenantId("tenant-a")

    with pytest.raises(ValidationError):
        Principal.model_validate({**principal.model_dump(), "tenant_id": ""})
