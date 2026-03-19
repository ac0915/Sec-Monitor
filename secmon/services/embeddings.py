from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EmbeddingPayload:
    provider: str
    model: str
    vector: list[float]
    dimensions: int
    raw_payload: dict


class BaseEmbeddingService:
    provider_name = "disabled"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.available = False
        self.selected_model = settings.embedding_model or ""
        self.unavailable_reason = "Embedding provider is not configured."

    def embed_text(self, text: str) -> EmbeddingPayload:
        raise NotImplementedError

    def _require_available(self) -> None:
        if self.available:
            return
        raise RuntimeError(self.unavailable_reason)


class DisabledEmbeddingService(BaseEmbeddingService):
    def __init__(self, settings: Settings, reason: str) -> None:
        super().__init__(settings)
        self.unavailable_reason = reason

    def embed_text(self, text: str) -> EmbeddingPayload:
        self._require_available()
        raise RuntimeError(self.unavailable_reason)


class GeminiEmbeddingService(BaseEmbeddingService):
    provider_name = "gemini"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.selected_model = settings.embedding_model or "gemini-embedding-001"
        self.client = None
        self.types = None

        if not settings.gemini_api_key:
            self.unavailable_reason = "GEMINI_API_KEY is not set."
            return

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            logger.warning("google-genai is not installed; Gemini embeddings disabled.")
            self.unavailable_reason = "google-genai is not installed."
            return

        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.types = types
        self.available = True

    def embed_text(self, text: str) -> EmbeddingPayload:
        self._require_available()
        if self.client is None or self.types is None:
            raise RuntimeError("Gemini embedding client is not initialized.")

        response = self.client.models.embed_content(
            model=self.selected_model,
            contents=text,
            config=self.types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
            ),
        )
        embeddings = getattr(response, "embeddings", None) or []
        vector = []
        if embeddings:
            vector = [float(item) for item in (getattr(embeddings[0], "values", None) or [])]
        return EmbeddingPayload(
            provider=self.provider_name,
            model=self.selected_model,
            vector=vector,
            dimensions=len(vector),
            raw_payload={
                "embedding_count": len(embeddings),
                "metadata": getattr(response, "metadata", None).model_dump() if getattr(response, "metadata", None) else None,
            },
        )


def build_embedding_service(settings: Settings) -> BaseEmbeddingService:
    provider = settings.embedding_provider.strip().lower()
    if provider == "gemini":
        return GeminiEmbeddingService(settings)
    if provider in {"disabled", "off", "none"}:
        return DisabledEmbeddingService(settings, "Embedding provider is disabled.")
    return DisabledEmbeddingService(settings, f"Unsupported embedding provider: {settings.embedding_provider}")
