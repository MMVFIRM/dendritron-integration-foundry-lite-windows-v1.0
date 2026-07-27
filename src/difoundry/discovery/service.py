from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json

from ..models import DiscoveryResult, DiscoverySource
from .asyncapi import AsyncAPIDiscoveryProvider
from .base import DiscoveryProvider
from .graphql import GraphQLDiscoveryProvider
from .json_schema import JSONSchemaDiscoveryProvider
from .openapi import OpenAPIDiscoveryProvider
from .sql import SQLDiscoveryProvider
from .system_profile import SystemProfileDiscoveryProvider


class DiscoveryService:
    """Provider registry and deterministic discovery facade.

    Additional providers can be registered without changing composition or runtime code.
    """

    def __init__(self, providers: Iterable[DiscoveryProvider] | None = None) -> None:
        self.providers: list[DiscoveryProvider] = list(
            providers
            or [
                SystemProfileDiscoveryProvider(),
                OpenAPIDiscoveryProvider(),
                AsyncAPIDiscoveryProvider(),
                GraphQLDiscoveryProvider(),
                SQLDiscoveryProvider(),
                JSONSchemaDiscoveryProvider(),
            ]
        )

    def register(self, provider: DiscoveryProvider, first: bool = False) -> None:
        if first:
            self.providers.insert(0, provider)
        else:
            self.providers.append(provider)

    def formats(self) -> list[str]:
        return sorted({format_name for provider in self.providers for format_name in provider.formats})

    def discover(self, source: DiscoverySource) -> DiscoveryResult:
        for provider in self.providers:
            if provider.can_handle(source):
                result = provider.discover(source)
                source_hash = self._source_hash(source.document)
                profile = result.profile.model_copy(
                    update={
                        "metadata": {
                            **result.profile.metadata,
                            "discovery_provider": provider.name,
                            "discovery_source_hash": source_hash,
                        }
                    }
                )
                return result.model_copy(update={"source_hash": source_hash, "profile": profile})
        raise ValueError(
            f"No discovery provider recognized source {source.source_id!r}. "
            f"Supported formats: {', '.join(self.formats())}"
        )

    @staticmethod
    def _source_hash(document: object) -> str:
        if isinstance(document, str):
            encoded = document.encode("utf-8")
        else:
            encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
