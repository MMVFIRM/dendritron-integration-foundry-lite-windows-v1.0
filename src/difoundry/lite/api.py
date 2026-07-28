from __future__ import annotations

import hmac
import ipaddress
import secrets
import webbrowser
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .service import LiteContext
from .settings import LiteSettings
from .oauth import (
    GOOGLE,
    MICROSOFT,
    SALESFORCE,
    google_sheets_profile,
    microsoft_365_profile,
    salesforce_profile,
)


class SystemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    base_url: str
    auth_kind: str = "none"
    credentials: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    source_system_id: str | None = None
    target_system_ids: list[str] = Field(default_factory=list)


class EnabledRequest(BaseModel):
    enabled: bool


class EventRequest(BaseModel):
    payload: dict[str, Any]


class AnswersRequest(BaseModel):
    answers: dict[str, Any]


class StartupRequest(BaseModel):
    enabled: bool


class OAuthConfigureRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str = Field(default="", max_length=1000)


def create_lite_app(context: LiteContext | None = None, shutdown_callback: Any | None = None) -> FastAPI:
    context = context or LiteContext.build()
    service = context.service
    assert service is not None
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service.start_runner()
        try:
            yield
        finally:
            service.stop_runner()

    app = FastAPI(
        title="Dendritron Foundry Lite",
        version="1.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.context = context
    app.state.session_token = secrets.token_urlsafe(32)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver", "testclient"]
        if not context.settings.allow_lan
        else ["*"],
    )
    static_root = files("difoundry.lite").joinpath("static")
    app.mount("/lite-static", StaticFiles(directory=str(static_root)), name="lite-static")

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        client = request.client.host if request.client else ""
        allowed = client in {"testclient", "testserver"}
        try:
            allowed = allowed or ipaddress.ip_address(client).is_loopback
        except ValueError:
            pass
        if not context.settings.allow_lan and not allowed:
            return Response("Foundry Lite is local-only by default", status_code=403)
        oauth_callback = request.url.path.startswith("/lite/oauth/") and request.url.path.endswith("/callback")
        if request.url.path.startswith("/lite/") and request.url.path not in {"/lite/liveness"} and not oauth_callback:
            cookie = request.cookies.get("foundry_lite_session")
            header = request.headers.get("X-Foundry-Lite-Session")
            if (
                not cookie
                or not header
                or not hmac.compare_digest(cookie, app.state.session_token)
                or not hmac.compare_digest(header, cookie)
            ):
                return Response("Local session token required", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/console", response_class=HTMLResponse)
    def console():
        response = FileResponse(str(static_root.joinpath("index.html")))
        response.set_cookie(
            "foundry_lite_session",
            app.state.session_token,
            samesite="strict",
            secure=False,
            httponly=False,
        )
        return response

    @app.get("/lite/liveness")
    def liveness():
        return {"status": "ok", "edition": "lite", "login_required": False}

    @app.get("/lite/overview")
    def overview():
        return service.overview()

    @app.get("/lite/catalog")
    def catalog(q: str = ""):
        return {"connectors": context.catalog.search(q)}

    def oauth_redirect_uri(provider_id: str, request: Request) -> str:
        provider = context.oauth.provider(provider_id)
        port = request.url.port
        default_port = (request.url.scheme == "http" and port == 80) or (request.url.scheme == "https" and port == 443)
        authority = provider.redirect_hostname if not port or default_port else f"{provider.redirect_hostname}:{port}"
        return f"{request.url.scheme}://{authority}/lite/oauth/{provider_id}/callback"

    @app.get("/lite/oauth/providers")
    def oauth_providers(request: Request):
        providers = context.oauth.status()
        for provider in providers:
            provider["callback_url"] = oauth_redirect_uri(provider["provider_id"], request)
        return {"providers": providers}

    @app.post("/lite/oauth/{provider_id}/configure")
    def oauth_configure(provider_id: str, body: OAuthConfigureRequest):
        try:
            return context.oauth.configure(provider_id, body.client_id, body.client_secret)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/lite/oauth/{provider_id}/start")
    def oauth_start(provider_id: str, request: Request):
        try:
            redirect_uri = oauth_redirect_uri(provider_id, request)
            return {"authorization_url": context.oauth.start(provider_id, redirect_uri)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/lite/oauth/{provider_id}/callback")
    def oauth_callback(
        provider_id: str,
        request: Request,
        state: str = "",
        code: str = "",
        error: str = "",
    ):
        if error:
            return RedirectResponse(url="/console?" + urlencode({"oauth": "error", "message": error}), status_code=303)
        try:
            redirect_uri = oauth_redirect_uri(provider_id, request)
            credentials = context.oauth.exchange(provider_id, state, code, redirect_uri)
            provider = context.oauth.provider(provider_id)
            system_id = context.database.new_id("sys")
            if provider_id == GOOGLE.provider_id:
                profile = google_sheets_profile(system_id)
            elif provider_id == SALESFORCE.provider_id:
                profile = salesforce_profile(system_id, credentials.get("instance_url"))
            elif provider_id == MICROSOFT.provider_id:
                profile = microsoft_365_profile(system_id)
            else:
                raise ValueError("Provider profile is not installed")
            service.add_profiled_system(
                provider.name,
                profile.base_url or provider.base_url,
                "oauth2",
                credentials,
                profile,
                "provider-oauth",
            )
            return RedirectResponse(url="/console?oauth=connected", status_code=303)
        except (ValueError, KeyError) as exc:
            return RedirectResponse(
                url="/console?" + urlencode({"oauth": "error", "message": str(exc)}), status_code=303
            )

    @app.post("/lite/systems/{system_id}/oauth/revoke")
    def oauth_revoke(system_id: str):
        try:
            provider_revoked = service.revoke_oauth_system(system_id)
            return {"revoked": True, "provider_revoked": provider_revoked}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/lite/systems")
    def add_system(body: SystemCreate):
        try:
            return service.add_system(body.name, body.base_url, body.auth_kind, body.credentials)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/lite/systems")
    def systems():
        return {"systems": service.list_systems()}

    @app.get("/lite/systems/{system_id}")
    def system(system_id: str):
        try:
            return service.get_system(system_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/lite/chat")
    def chat(body: ChatRequest):
        return service.chat(body.message, body.source_system_id, body.target_system_ids)

    @app.get("/lite/chat")
    def chat_history():
        return {"messages": service.chat_history()}

    @app.get("/lite/connections")
    def connections():
        return {"connections": service.list_connections()}

    @app.get("/lite/connections/{connection_id}")
    def connection(connection_id: str):
        try:
            return service.get_connection(connection_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.put("/lite/connections/{connection_id}/enabled")
    def enable(connection_id: str, body: EnabledRequest):
        try:
            return service.set_enabled(connection_id, body.enabled)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/lite/connections/{connection_id}/answers")
    def answer_questions(connection_id: str, body: AnswersRequest):
        try:
            return service.answer_questions(connection_id, body.answers)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/lite/connections/{connection_id}/export")
    def export_connection(connection_id: str):
        try:
            path = service.export_connection(connection_id)
            return FileResponse(path, filename=path.name, media_type="application/zip")
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/lite/connections/{connection_id}/test")
    def test_connection(connection_id: str):
        try:
            return service.get_connection(connection_id)["preview"]
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete("/lite/connections/{connection_id}", status_code=204)
    def delete(connection_id: str):
        try:
            service.delete_connection(connection_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/lite/activity")
    def activity(limit: int = 50):
        return {"activity": service.activities(max(1, min(limit, 200)))}

    @app.get("/lite/desktop")
    def desktop_status():
        return service.desktop_status()

    @app.post("/lite/desktop/backup")
    def desktop_backup():
        path = service.create_backup("manual")
        return {"created": True, "filename": path.name}

    @app.get("/lite/desktop/support")
    def desktop_support():
        path = service.create_support_bundle()
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @app.put("/lite/desktop/startup")
    def desktop_startup(body: StartupRequest):
        from .desktop_state import set_startup_enabled
        try:
            return {"enabled": set_startup_enabled(body.enabled)}
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/lite/desktop/open-data")
    def desktop_open_data():
        import os
        import subprocess
        import sys
        path = context.settings.data_dir
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            raise HTTPException(409, f"Could not open data folder: {exc}") from exc
        return {"opened": True}

    @app.post("/lite/desktop/shutdown", status_code=202)
    def desktop_shutdown():
        if shutdown_callback is None:
            raise HTTPException(409, "This process is not managed by the desktop launcher")
        from threading import Thread
        Thread(target=shutdown_callback, daemon=True).start()
        return {"shutting_down": True}

    @app.post("/lite/hooks/{connection_id}/{token}")
    def hook(connection_id: str, token: str, body: EventRequest):
        try:
            connection = service.get_connection(connection_id)
            expected = connection["webhook_path"].rsplit("/", 1)[-1]
            if not hmac.compare_digest(token, expected):
                raise HTTPException(404, "Not found")
            return service.enqueue(connection_id, body.payload)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return app


def main() -> None:
    settings = LiteSettings.from_env()
    url = f"http://{settings.host}:{settings.port}/console"
    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.allow_lan:
        raise SystemExit("Refusing non-loopback bind unless DIFOUNDRY_LITE_ALLOW_LAN=true")
    if settings.open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(create_lite_app(), host=settings.host, port=settings.port, proxy_headers=False)
