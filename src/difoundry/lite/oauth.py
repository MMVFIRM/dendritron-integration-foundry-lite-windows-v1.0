from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from ..discovery.schema import object_from_schema
from ..models import AuthenticationProfile, OperationProfile, SystemProfile
from .database import LiteDatabase, now_iso
from .vault import LocalVault


@dataclass(frozen=True, slots=True)
class OAuthProvider:
    provider_id: str
    name: str
    authorization_url: str
    token_url: str
    revoke_url: str
    base_url: str
    scopes: tuple[str, ...]
    client_secret_required: bool
    uses_pkce: bool
    setup_url: str
    setup_instructions: str
    client_secret_optional: bool = False
    redirect_hostname: str = "127.0.0.1"
    authorization_parameters: tuple[tuple[str, str], ...] = ()
    revoke_method: str = "post"


GOOGLE = OAuthProvider(
    provider_id="google-sheets",
    name="Google Sheets",
    authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    revoke_url="https://oauth2.googleapis.com/revoke",
    base_url="https://sheets.googleapis.com/v4",
    scopes=(
        "openid",
        "email",
        "https://www.googleapis.com/auth/spreadsheets",
    ),
    client_secret_required=False,
    uses_pkce=True,
    setup_url="https://console.cloud.google.com/apis/credentials",
    setup_instructions="Create a Desktop app OAuth client, enable the Google Sheets API, then paste its client ID here. A desktop client secret is optional and is not confidential.",
    client_secret_optional=True,
    authorization_parameters=(("access_type", "offline"), ("prompt", "consent")),
)

SALESFORCE = OAuthProvider(
    provider_id="salesforce",
    name="Salesforce",
    authorization_url="https://login.salesforce.com/services/oauth2/authorize",
    token_url="https://login.salesforce.com/services/oauth2/token",
    revoke_url="https://login.salesforce.com/services/oauth2/revoke",
    base_url="https://login.salesforce.com/services/data/v61.0",
    scopes=("api", "refresh_token", "offline_access"),
    client_secret_required=False,
    uses_pkce=True,
    setup_url="https://login.salesforce.com/lightning/setup/ExternalClientApps/home",
    setup_instructions="Create a Salesforce External Client App for a desktop/public client, add the callback below, enable OAuth and PKCE, and paste the consumer key. Do not require a client secret.",
    authorization_parameters=(("prompt", "login consent"),),
)

MICROSOFT = OAuthProvider(
    provider_id="microsoft-365",
    name="Microsoft 365",
    authorization_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    revoke_url="",
    base_url="https://graph.microsoft.com/v1.0",
    scopes=("offline_access", "User.Read", "Calendars.ReadWrite"),
    client_secret_required=False,
    uses_pkce=True,
    setup_url="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    setup_instructions="Register a public desktop application in Microsoft Entra, add the callback below under Mobile and desktop applications, allow personal or organizational accounts as needed, and paste the application client ID.",
    authorization_parameters=(("prompt", "select_account"),),
)


def google_sheets_profile(system_id: str) -> SystemProfile:
    row_schema = {
        "type": "object",
        "properties": {
            "spreadsheet_id": {"type": "string"},
            "range": {"type": "string"},
            "values": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        },
        "required": ["spreadsheet_id", "range", "values"],
    }
    row_object = object_from_schema("sheet_row", row_schema, name="Sheet Row")
    permissions = ["https://www.googleapis.com/auth/spreadsheets"]
    return SystemProfile(
        system_id=system_id,
        name="Google Sheets",
        protocol="rest",
        base_url=GOOGLE.base_url,
        authentication=AuthenticationProfile(kind="oauth2", secret_refs={"token": "token"}),
        objects=[row_object],
        operations=[
            OperationProfile(
                operation_id="read_sheet_values",
                method="GET",
                path="/spreadsheets/{spreadsheet_id}/values/{range}",
                object_id="sheet_row",
                operation_kind="list",
                required_permissions=permissions,
            ),
            OperationProfile(
                operation_id="append_sheet_values",
                method="POST",
                path="/spreadsheets/{spreadsheet_id}/values/{range}:append",
                object_id="sheet_row",
                operation_kind="create",
                request_schema=row_schema,
                required_permissions=permissions,
                metadata={"query_parameters": {"valueInputOption": "USER_ENTERED"}},
            ),
            OperationProfile(
                operation_id="update_sheet_values",
                method="PUT",
                path="/spreadsheets/{spreadsheet_id}/values/{range}",
                object_id="sheet_row",
                operation_kind="update",
                request_schema=row_schema,
                required_permissions=permissions,
                metadata={"query_parameters": {"valueInputOption": "USER_ENTERED"}},
            ),
        ],
        metadata={"provider": GOOGLE.provider_id, "discovery_format": "provider_profile"},
    )


def salesforce_profile(system_id: str, instance_url: str | None = None) -> SystemProfile:
    contact_schema = {
        "type": "object",
        "properties": {
            "Id": {"type": "string"},
            "Email": {"type": "string"},
            "FirstName": {"type": "string"},
            "LastName": {"type": "string"},
            "Company": {"type": "string"},
        },
        "required": ["LastName"],
    }
    contact = object_from_schema("contact", contact_schema, name="Contact").model_copy(update={"identifiers": ["Id"]})
    base_url = (instance_url or "https://login.salesforce.com").rstrip("/") + "/services/data/v61.0"
    return SystemProfile(
        system_id=system_id,
        name=SALESFORCE.name,
        protocol="rest",
        base_url=base_url,
        authentication=AuthenticationProfile(kind="oauth2", secret_refs={"token": "token"}),
        objects=[contact],
        operations=[
            OperationProfile(operation_id="query_contacts", method="GET", path="/query", object_id="contact", operation_kind="list", required_permissions=["api"]),
            OperationProfile(operation_id="create_contact", method="POST", path="/sobjects/Contact", object_id="contact", operation_kind="create", request_schema=contact_schema, required_permissions=["api"]),
            OperationProfile(operation_id="update_contact", method="PATCH", path="/sobjects/Contact/{Id}", object_id="contact", operation_kind="update", request_schema=contact_schema, required_permissions=["api"]),
        ],
        metadata={"provider": SALESFORCE.provider_id, "discovery_format": "provider_profile"},
    )


def microsoft_365_profile(system_id: str) -> SystemProfile:
    event_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "object"},
            "start": {"type": "object"},
            "end": {"type": "object"},
            "attendees": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["subject", "start", "end"],
    }
    event = object_from_schema("calendar_event", event_schema, name="Calendar Event").model_copy(
        update={"identifiers": ["id"]}
    )
    permissions = ["Calendars.ReadWrite"]
    return SystemProfile(
        system_id=system_id,
        name=MICROSOFT.name,
        protocol="rest",
        base_url=MICROSOFT.base_url,
        authentication=AuthenticationProfile(kind="oauth2", secret_refs={"token": "token"}),
        objects=[event],
        operations=[
            OperationProfile(operation_id="list_calendar_events", method="GET", path="/me/events", object_id="calendar_event", operation_kind="list", required_permissions=permissions),
            OperationProfile(operation_id="create_calendar_event", method="POST", path="/me/events", object_id="calendar_event", operation_kind="create", request_schema=event_schema, required_permissions=permissions),
            OperationProfile(operation_id="update_calendar_event", method="PATCH", path="/me/events/{id}", object_id="calendar_event", operation_kind="update", request_schema=event_schema, required_permissions=permissions),
        ],
        metadata={"provider": MICROSOFT.provider_id, "discovery_format": "provider_profile"},
    )


class OAuthManager:
    """Local OAuth coordinator. Public clients use PKCE; secrets never leave this computer."""

    def __init__(
        self,
        database: LiteDatabase,
        vault: LocalVault,
        client_factory: Callable[..., httpx.Client] | None = None,
    ) -> None:
        self.database = database
        self.vault = vault
        self.client_factory = client_factory or httpx.Client
        self.providers = {item.provider_id: item for item in (GOOGLE, MICROSOFT, SALESFORCE)}
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def provider(self, provider_id: str) -> OAuthProvider:
        try:
            return self.providers[provider_id]
        except KeyError as exc:
            raise ValueError("Unsupported OAuth provider") from exc

    def configure(self, provider_id: str, client_id: str, client_secret: str = "") -> dict[str, Any]:
        provider = self.provider(provider_id)
        client_id = client_id.strip()
        client_secret = client_secret.strip()
        if not client_id:
            raise ValueError("OAuth client ID is required")
        if provider.client_secret_required and not client_secret:
            raise ValueError(f"{provider.name} requires the client secret from your organization's app")
        existing = self.database.one(
            "SELECT secret_ref FROM lite_oauth_providers WHERE provider_id=?", (provider_id,)
        )
        secret_ref = self.vault.put(
            {"client_secret": client_secret}, existing["secret_ref"] if existing else None
        )
        with self.database.transaction() as db:
            db.execute(
                "INSERT INTO lite_oauth_providers(provider_id,client_id,secret_ref,scopes_json,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(provider_id) DO UPDATE SET client_id=excluded.client_id,secret_ref=excluded.secret_ref,"
                "scopes_json=excluded.scopes_json,updated_at=excluded.updated_at",
                (provider_id, client_id, secret_ref, self.database.dumps(provider.scopes), now_iso()),
            )
        return {"provider_id": provider_id, "name": provider.name, "configured": True}

    def status(self) -> list[dict[str, Any]]:
        configured = {
            row["provider_id"]: row
            for row in self.database.all("SELECT provider_id,client_id,updated_at FROM lite_oauth_providers")
        }
        return [
            {
                "provider_id": provider.provider_id,
                "name": provider.name,
                "configured": provider.provider_id in configured,
                "client_id": configured.get(provider.provider_id, {}).get("client_id"),
                "scopes": list(provider.scopes),
                "client_secret_required": provider.client_secret_required,
                "client_secret_optional": provider.client_secret_optional,
                "setup_url": provider.setup_url,
                "setup_instructions": provider.setup_instructions,
                "local_only": True,
            }
            for provider in self.providers.values()
        ]

    def _configuration(self, provider_id: str) -> tuple[str, str]:
        row = self.database.one("SELECT * FROM lite_oauth_providers WHERE provider_id=?", (provider_id,))
        if not row:
            raise ValueError("Configure this OAuth provider first")
        return row["client_id"], self.vault.resolve(row["secret_ref"]).get("client_secret", "")

    def start(self, provider_id: str, redirect_uri: str) -> str:
        provider = self.provider(provider_id)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        client_id, _ = self._configuration(provider_id)
        with self._lock:
            self._pending[state] = {
                "provider_id": provider_id,
                "verifier": verifier,
                "redirect_uri": redirect_uri,
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
            }
        parameters = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(provider.scopes),
            "state": state,
            **dict(provider.authorization_parameters),
        }
        if provider.uses_pkce:
            parameters.update({"code_challenge": challenge, "code_challenge_method": "S256"})
        query = urlencode(parameters)
        return f"{provider.authorization_url}?{query}"

    def exchange(self, provider_id: str, state: str, code: str, redirect_uri: str) -> dict[str, Any]:
        provider = self.provider(provider_id)
        if not code:
            raise ValueError("OAuth provider returned no authorization code")
        with self._lock:
            pending = self._pending.pop(state, None)
        if not pending or pending["provider_id"] != provider_id:
            raise ValueError("OAuth state is invalid or has already been used")
        if pending["expires_at"] < datetime.now(timezone.utc):
            raise ValueError("OAuth authorization expired; please try again")
        if not secrets.compare_digest(pending["redirect_uri"], redirect_uri):
            raise ValueError("OAuth redirect URI mismatch")
        client_id, client_secret = self._configuration(provider_id)
        token_request = {
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if client_secret:
            token_request["client_secret"] = client_secret
        if provider.uses_pkce:
            token_request["code_verifier"] = pending["verifier"]
        with self.client_factory(timeout=20, follow_redirects=True) as client:
            response = client.post(provider.token_url, data=token_request)
        if response.status_code >= 400:
            raise ValueError(f"{provider.name} rejected the OAuth code exchange")
        token = response.json()
        if not token.get("access_token"):
            raise ValueError("OAuth provider returned no access token")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in", 3600)))
        credentials = {
            "token": token["access_token"],
            "refresh_token": token.get("refresh_token"),
            "token_type": token.get("token_type", "Bearer"),
            "scope": token.get("scope", " ".join(provider.scopes)),
            "expires_at": expires_at.isoformat(),
            "provider_id": provider_id,
        }
        if token.get("instance_url"):
            credentials["instance_url"] = token["instance_url"]
        return credentials

    def refresh(self, credentials: dict[str, Any]) -> dict[str, Any]:
        expires = credentials.get("expires_at")
        if not expires or datetime.fromisoformat(expires) > datetime.now(timezone.utc) + timedelta(minutes=2):
            return credentials
        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            raise ValueError("OAuth access expired and no refresh token is available; reconnect the system")
        provider_id = credentials["provider_id"]
        provider = self.provider(provider_id)
        client_id, client_secret = self._configuration(provider_id)
        token_request = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if client_secret:
            token_request["client_secret"] = client_secret
        with self.client_factory(timeout=20, follow_redirects=True) as client:
            response = client.post(provider.token_url, data=token_request)
        if response.status_code >= 400:
            raise ValueError("OAuth token refresh failed; reconnect the system")
        token = response.json()
        updated = dict(credentials)
        updated["token"] = token["access_token"]
        updated["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in", 3600)))
        ).isoformat()
        if token.get("refresh_token"):
            updated["refresh_token"] = token["refresh_token"]
        return updated

    def revoke(self, credentials: dict[str, Any]) -> bool:
        provider = self.provider(credentials["provider_id"])
        token = credentials.get("refresh_token") or credentials.get("token")
        if not token:
            return False
        if not provider.revoke_url:
            return False
        with self.client_factory(timeout=20, follow_redirects=True) as client:
            if provider.revoke_method == "delete_path":
                response = client.delete(f"{provider.revoke_url}/{token}")
            else:
                response = client.post(provider.revoke_url, data={"token": token})
        if response.status_code >= 400:
            raise ValueError("OAuth provider could not revoke this connection")
        return True
