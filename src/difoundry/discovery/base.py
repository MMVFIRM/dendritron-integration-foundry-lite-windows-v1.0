from __future__ import annotations

from typing import Protocol

from ..models import DiscoveryResult, DiscoverySource


class DiscoveryProvider(Protocol):
    name: str
    formats: tuple[str, ...]

    def can_handle(self, source: DiscoverySource) -> bool: ...

    def discover(self, source: DiscoverySource) -> DiscoveryResult: ...
