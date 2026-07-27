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
    """Provider hints only. Live discovery always verifies the connected system."""

    def __init__(self, entries: list[CatalogEntry] | None = None):
        self.entries = entries or [
            CatalogEntry("hubspot", "HubSpot", ("hubspot crm",), "bearer", "https://api.hubapi.com", notes="Use a private-app access token or configured OAuth connection."),
            CatalogEntry("slack", "Slack", ("slack api",), "bearer", "https://slack.com/api", notes="Use a bot or user token with only the scopes the connection needs."),
            CatalogEntry("stripe", "Stripe", ("stripe payments",), "bearer", "https://api.stripe.com/v1", notes="Use a restricted API key when possible."),
            CatalogEntry("github", "GitHub", ("github api",), "bearer", "https://api.github.com", notes="Use a fine-grained personal access token or GitHub App token."),
            CatalogEntry("microsoft-graph", "Microsoft 365 / Graph", ("microsoft graph", "office 365", "m365"), "bearer", "https://graph.microsoft.com/v1.0", discovery_paths=("/$metadata",)),
            CatalogEntry("notion", "Notion", ("notion api",), "bearer", "https://api.notion.com/v1"),
            CatalogEntry("airtable", "Airtable", ("airtable api",), "bearer", "https://api.airtable.com/v0"),
            CatalogEntry("quickbooks", "QuickBooks Online", ("quickbooks", "intuit"), "bearer", "https://quickbooks.api.intuit.com/v3", notes="Requires an Intuit OAuth access token and company context."),
            CatalogEntry("salesforce", "Salesforce", ("salesforce crm",), "bearer", notes="Enter your organization instance URL after authorization."),
            CatalogEntry("shopify", "Shopify", ("shopify admin",), "api_key", notes="Enter the shop Admin API base URL and access token."),
            CatalogEntry("zendesk", "Zendesk", ("zendesk support",), "bearer", notes="Enter your Zendesk subdomain API URL."),
            CatalogEntry("generic-rest", "Custom REST API", ("rest api", "custom api"), "api_key"),
            CatalogEntry("generic-graphql", "Custom GraphQL", ("graphql api",), "bearer", discovery_paths=("/graphql",)),
            CatalogEntry("generic-odata", "OData Service", ("odata",), "bearer", discovery_paths=("/$metadata",)),
            CatalogEntry("generic-local", "Internal System", ("internal app", "internal crm", "custom system"), "api_key"),
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
