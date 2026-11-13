import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.public.graph import build_public_assistant_graph
from app.api.routes import auth as user_auth
from app.api.routes import booking, chat, chat_session, health, widget
from app.api.routes import live_chat as live_chat_routes
from app.api.routes.admin import (
    audit_log,
    google_oauth,
)
from app.api.routes.admin import (
    bookings as admin_bookings,
)
from app.api.routes.admin import (
    calendar as admin_calendar,
)
from app.api.routes.admin import (
    clients as admin_clients,
)
from app.api.routes.admin import (
    consent as admin_consent,
)
from app.api.routes.admin import (
    dashboard as admin_dashboard,
)
from app.api.routes.admin import (
    documents as admin_documents,
)
from app.api.routes.admin import (
    faq_gaps as admin_faq_gaps,
)
from app.api.routes.admin import (
    faqs as admin_faqs,
)
from app.api.routes.admin import (
    inbox as admin_inbox,
)
from app.api.routes.admin import (
    notification_recipients as admin_notification_recipients,
)
from app.api.routes.admin import (
    settings as admin_settings,
)
from app.api.routes.admin import (
    sources as admin_sources,
)
from app.api.routes.admin import (
    users as admin_users,
)
from app.api.routes.admin import (
    widget as admin_widget,
)
from app.core.config import get_settings
from app.core.csrf import CSRFOriginMiddleware
from app.db.init import init_db
from app.services import live_chat
from app.services.email import log_email_configuration
from app.services.notifications import run_dispatcher
from app.services.website_crawler.scheduler import run_scheduler

# Cookie-authenticated paths that change state; see CSRFOriginMiddleware.
CSRF_PROTECTED_PREFIXES = ("/api/v1/admin", "/api/v1/auth")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log_email_configuration()
    if settings.app_env == "production" and "localhost" in settings.widget_public_url:
        logger.error("WIDGET_PUBLIC_URL is still localhost. Install snippets will not work.")
    await init_db()
    # Times out overdue live handovers into callbacks every 15 seconds (single-process broker).
    sweeper = asyncio.create_task(live_chat.run_sweeper())
    # Sends queued notification emails, with retries (FR-34).
    notifier = asyncio.create_task(run_dispatcher())
    # Monthly re-crawl of client websites. Only one instance runs it (advisory lock).
    crawl_scheduler = asyncio.create_task(run_scheduler())
    try:
        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            await checkpointer.setup()
            app.state.agent_graph = build_public_assistant_graph(checkpointer)
            logger.info("Public assistant graph ready")
            yield
    finally:
        for task in (sweeper, notifier, crawl_scheduler):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="Dental & Orthodontic AI Assistant API",
        description="Embeddable RAG assistant for public website documentation",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Added before CORS so CORS stays outermost and blocked responses still carry CORS headers.
    app.add_middleware(
        CSRFOriginMiddleware,
        allowed_origins=settings.allowed_origins_list,
        protected_prefixes=CSRF_PROTECTED_PREFIXES,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Lets the admin login page read how long a lockout lasts.
        expose_headers=["Retry-After"],
    )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(chat_session.router, prefix="/api/v1")
    app.include_router(admin_consent.router, prefix="/api/v1")
    app.include_router(admin_faq_gaps.router, prefix="/api/v1")
    app.include_router(admin_inbox.router, prefix="/api/v1")
    app.include_router(admin_inbox.callbacks_router, prefix="/api/v1")
    app.include_router(admin_notification_recipients.router, prefix="/api/v1")
    app.include_router(admin_widget.router, prefix="/api/v1")
    app.include_router(live_chat_routes.router, prefix="/api/v1")
    app.include_router(widget.router, prefix="/api/v1")
    app.include_router(user_auth.router, prefix="/api/v1")
    app.include_router(admin_clients.router, prefix="/api/v1")
    app.include_router(admin_users.router, prefix="/api/v1")
    app.include_router(admin_documents.router, prefix="/api/v1")
    app.include_router(admin_faqs.router, prefix="/api/v1")
    app.include_router(google_oauth.router, prefix="/api/v1")
    app.include_router(admin_calendar.router, prefix="/api/v1")
    app.include_router(admin_settings.router, prefix="/api/v1")
    app.include_router(admin_sources.router, prefix="/api/v1")
    app.include_router(admin_bookings.router, prefix="/api/v1")
    app.include_router(admin_dashboard.router, prefix="/api/v1")
    app.include_router(audit_log.router, prefix="/api/v1")
    app.include_router(booking.router, prefix="/api/v1")

    return app


app = create_app()
