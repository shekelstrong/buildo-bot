"""Buildo bot API — FastAPI app for web dashboard and Mini App.

Exposed on port 8888 (separate from internal health on 9090).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bot.api.routes import auth, sites

logger = logging.getLogger(__name__)


def create_api_app() -> FastAPI:
    """Build the public-facing API app."""
    app = FastAPI(
        title="Buildo API",
        version="0.1.0-mvp",
        description="Backend API for buildo-web dashboard and buildo-miniapp",
    )

    # CORS for web dashboard and Telegram Mini App
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://buildo.ru",
            "https://www.buildo.ru",
            "https://buildo-web.vercel.app",
            "https://buildo-miniapp.layero.app",
            "https://web.telegram.org",  # Telegram WebApp host
            "http://localhost:3000",  # dev
            "http://localhost:5173",  # miniapp dev
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(sites.router)
    app.include_router(auth.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "buildo-api", "version": "0.1.0-mvp"}

    return app
