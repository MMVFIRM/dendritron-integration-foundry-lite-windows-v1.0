from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .production.api import router as production_router
from .production.middleware import ProductionSecurityMiddleware
from .production.settings import ProductionSettings


def create_app(settings: ProductionSettings | None = None) -> FastAPI:
    """Production app. Phase 0–5 developer routes are intentionally not mounted here."""
    settings = settings or ProductionSettings.from_env()
    settings.assert_startup_safe()
    production = settings.environment == "production"
    app = FastAPI(
        title="Dendritron Integration Foundry",
        version="0.7.2",
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.include_router(production_router)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Bootstrap-Token"],
        )
    app.add_middleware(
        ProductionSecurityMiddleware,
        max_request_bytes=settings.max_request_bytes,
        production=settings.environment == "production",
    )
    return app


app = create_app()
