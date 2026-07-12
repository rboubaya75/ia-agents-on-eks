from collections.abc import Sequence

from ia_application import (
    EmbeddingProfile,
    EmbeddingRequest,
    EmbeddingResponse,
    ExtractedDocument,
)
from ia_domain import Document, DocumentId, TenantId


class FakeEmbeddingProvider:
    def __init__(
        self,
        responses: Sequence[EmbeddingResponse],
        *,
        profile: EmbeddingProfile | None = None,
    ) -> None:
        self._responses = list(responses)
        first_response = self._responses[0] if self._responses else None
        self._profile = profile or EmbeddingProfile(
            alias="default",
            revision="fake-profile-v1",
            model_id=(
                first_response.model_id if first_response is not None else "fake-embedding-model"
            ),
            dimensions=first_response.dimensions if first_response is not None else 2,
        )
        self.profile_requests: list[str] = []
        self.requests: list[EmbeddingRequest] = []

    async def resolve_profile(self, model_alias: str) -> EmbeddingProfile:
        self.profile_requests.append(model_alias)
        return self._profile.model_copy(update={"alias": model_alias})

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        if not self._responses:
            msg = "no fake embedding response configured"
            raise RuntimeError(msg)
        return self._responses.pop(0)


class FakeTextExtractor:
    def __init__(
        self,
        responses: dict[tuple[TenantId, DocumentId, str], ExtractedDocument],
    ) -> None:
        self._responses = responses.copy()
        self.requests: list[Document] = []

    async def extract(self, document: Document) -> ExtractedDocument:
        self.requests.append(document)
        key = (document.tenant_id, document.document_id, document.source_version)
        try:
            return self._responses[key]
        except KeyError as error:
            msg = "no fake extraction configured"
            raise RuntimeError(msg) from error
