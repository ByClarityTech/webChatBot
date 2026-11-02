import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.public.graph import build_public_assistant_graph
from app.api.routes import booking, chat, documents, health
from app.api.routes.admin import (
    audit_log,
    auth,
    bookings as admin_bookings,
    dashboard as admin_dashboard,
    documents as admin_documents,
    google_oauth,
    settings as admin_settings,
)
from app.core.config import get_settings
from app.db.init import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        await checkpointer.setup()
        app.state.agent_graph = build_public_assistant_graph(checkpointer)
        logger.info("Public assistant graph ready")
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="Public Docs Chatbot API",
        description="Embeddable RAG assistant for public website documentation",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(admin_documents.router, prefix="/api/v1")
    app.include_router(google_oauth.router, prefix="/api/v1")
    app.include_router(admin_settings.router, prefix="/api/v1")
    app.include_router(admin_bookings.router, prefix="/api/v1")
    app.include_router(admin_dashboard.router, prefix="/api/v1")
    app.include_router(audit_log.router, prefix="/api/v1")
    app.include_router(booking.router, prefix="/api/v1")

    return app


app = create_app()
