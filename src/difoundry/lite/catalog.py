from __future__ import annotations

from dataclasses import dataclass

from ..naming import slugify


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    connector_id: str
    name: str
    aliases: tuple[str, ...] = ()
    auth_kind: str = "oauth2"
    base_url: str | None = None
    discovery_paths: tuple[str, ...] = ()
    notes: str = ""


class ConnectorCatalog:
    """Connectors the free local edition can actually authorize or discover."""

    def __init__(self, entries: list[CatalogEntry] | None = None):
        self.entries = entries or [
            CatalogEntry("google-sheets", "Google Sheets", ("sheets", "google spreadsheet"), "oauth2", "https://sheets.googleapis.com/v4", notes="Local desktop OAuth with PKCE; requires a Google Desktop app client ID."),
            CatalogEntry("microsoft-365", "Microsoft 365", ("microsoft graph", "office 365", "m365"), "oauth2", "https://graph.microsoft.com/v1.0", notes="Local public-client OAuth with PKCE; calendar operations are supported."),
            CatalogEntry("salesforce", "Salesforce", ("salesforce crm",), "oauth2", notes="Local public-client OAuth with PKCE using your Salesforce External Client App."),
            CatalogEntry("generic-rest", "Custom REST API", ("rest api", "custom api"), "api_key"),
            CatalogEntry("generic-graphql", "Custom GraphQL", ("graphql api",), "bearer", discovery_paths=("/graphql",)),
            CatalogEntry("generic-odata", "OData Service", ("odata",), "bearer", discovery_paths=("/$metadata",)),
        ]

    def resolve(self, name: str) -> CatalogEntry | None:
        normalized = slugify(name)
        for entry in self.entries:
            values = {slugify(entry.name), slugify(entry.connector_id), *(slugify(alias) for alias in entry.aliases)}
            if normalized in values:
                return entry
        return None

    def search(self, query: str) -> list[dict[str, str | None]]:
        needle = slugify(query) if query.strip() else ""
        result = []
        for entry in self.entries:
            hay = {slugify(entry.name), slugify(entry.connector_id), *(slugify(alias) for alias in entry.aliases)}
            if not needle or any(needle in value for value in hay):
                result.append({
                    "connector_id": entry.connector_id,
                    "name": entry.name,
                    "auth_kind": entry.auth_kind,
                    "base_url": entry.base_url,
                    "notes": entry.notes,
                })
        return result
