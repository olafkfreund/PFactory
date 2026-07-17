"""
PFactory Web Server - FastAPI Application.

Main entry point for the web server that provides:
- REST API for project/task management
- WebSocket endpoints for real-time streaming
- Static file serving for the React SPA
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from . import env_bootstrap  # noqa: F401  — loads .env into os.environ first
from .auth import TokenAuthMiddleware
from .config import get_settings
from .database.engine import init_db
from .logging_config import setup_logging
from .routes import (
    api_keys,
    audit,
    auth_routes,
    auto_fix,
    capabilities,
    cloud,
    context,
    docs_targets,
    email,
    execution,
    files,
    git,
    git_credentials,
    github,
    mcp,
    mcp_rpc,
    notifications,
    organizations,
    projects,
    provider_runtimes,
    search,
    skills,
    tasks,
    terminal,
    test_target_credentials,
    visual_inspection,
)
from .routes import cli_accounts as cli_accounts_routes
from .routes import llm_endpoints as llm_endpoints_routes
from .routes import logs as logs_routes
from .routes import settings as settings_routes
from .services.skills_service import init_skills_service
from .websockets import events as events_ws
from .websockets import logs as logs_ws
from .websockets import progress as progress_ws
from .websockets import terminal as terminal_ws

# v3.0.2 — logging is configured INSIDE create_app() (was at module
# level until v3.0.1). Module-level setup_logging() was an import-
# side-effect that clobbered pytest's caplog handler whenever this
# module was imported during a test session, breaking ~7 unrelated
# stdlib-logging tests. Moving the call inside the factory means
# importing this module is a pure operation.
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    settings = get_settings()

    # Startup
    logger.info("Starting PFactory Web Server...")
    logger.info(f"Backend path: {settings.BACKEND_PATH}")
    logger.info(f"Projects data dir: {settings.PROJECTS_DATA_DIR}")

    # Ensure data directory exists
    Path(settings.PROJECTS_DATA_DIR).mkdir(parents=True, exist_ok=True)

    # Auto-configure autoBuildPath if the backend directory exists
    # (enables project initialization without manual settings configuration)
    backend_path = Path(settings.BACKEND_PATH)
    if backend_path.exists():
        from .routes.settings import load_app_settings, save_app_settings
        app_settings = load_app_settings()
        if not app_settings.autoBuildPath:
            app_settings.autoBuildPath = str(backend_path)
            save_app_settings(app_settings)
            logger.info(f"Auto-configured autoBuildPath: {backend_path}")

    # Initialize database (creates tables if needed)
    await init_db()

    # Initialize skills service singleton once at startup
    init_skills_service()
    logger.info("SkillsService initialized")

    # Liveness watchdog driver (#95): periodically flag silent stages as
    # `stalled`. OFF by default; opt in with APP_LIVENESS_SWEEP_ENABLED.
    app.state.liveness_sweep_task = None
    if settings.LIVENESS_SWEEP_ENABLED:
        from .background.liveness_sweep import liveness_sweep_loop

        app.state.liveness_sweep_task = asyncio.create_task(
            liveness_sweep_loop(
                settings.LIVENESS_SWEEP_INTERVAL_SECONDS,
                settings.LIVENESS_SWEEP_DEADLINE_SECONDS,
            )
        )
        logger.info(
            "Liveness sweep enabled (every %ss, deadline %ss)",
            settings.LIVENESS_SWEEP_INTERVAL_SECONDS,
            settings.LIVENESS_SWEEP_DEADLINE_SECONDS,
        )

    yield

    # Shutdown
    sweep_task = getattr(app.state, "liveness_sweep_task", None)
    if sweep_task is not None:
        sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass

    logger.info("Shutting down PFactory Web Server...")


def _read_app_version() -> str:
    """Return the canonical package version.

    Reads ``apps/backend/__init__.py``'s ``__version__`` — that's the
    file bump-version.js updates on every release. We deliberately
    don't ``from apps.backend import __version__`` because the
    web-server's PYTHONPATH doesn't reliably include the repo root in
    every install layout (especially the container image). Reading
    the file via a relative path keeps it robust.
    """
    import re
    from pathlib import Path

    backend_init = Path(__file__).resolve().parents[2] / "backend" / "__init__.py"
    try:
        content = backend_init.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
    except OSError:
        pass
    return "0.0.0-unknown"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    # v3.0.2 — stdlib logging configured here (was module-level
    # in v3.0.0/v3.0.1; see note at the top of this file).
    setup_logging(log_level="DEBUG" if settings.DEBUG else "INFO")

    # Epic #26 P6 (wired in v3.0.2) — structlog JSON-to-stdout logging.
    # Configured here so every log line emitted during create_app() +
    # lifespan startup is JSON-formatted from the very first event.
    # Idempotent: re-calling overrides the processor chain wholesale.
    from .observability import configure_structlog
    configure_structlog(level="DEBUG" if settings.DEBUG else "INFO")

    # Version comes from apps/backend/__init__.py — the canonical source
    # of truth that bump-version.js updates on every release. Reading it
    # at runtime avoids the v3.0.0/v3.0.1 drift where main.py's
    # hardcoded "1.0.0" lagged behind the actual package version.
    _app_version = _read_app_version()

    app = FastAPI(
        title="PFactory Web API",
        description="Web API for PFactory — self-hosted AI task management + agent orchestration",
        version=_app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    # Add CORS middleware.
    # CORS guard (issue #128): a wildcard origin ("*") combined with
    # allow_credentials=True is forbidden by the CORS spec and is a credential-
    # leak footgun — browsers ignore the combination, but a permissive server
    # config should never advertise it. Strip any wildcard from the configured
    # origins before enabling credentialed CORS.
    cors_origins = [o for o in settings.CORS_ORIGINS if o != "*"]
    if cors_origins != list(settings.CORS_ORIGINS):
        logger.warning(
            "Stripped wildcard ('*') CORS origin: it is incompatible with "
            "allow_credentials=True (issue #128). Configure explicit origins "
            "via APP_CORS_ORIGINS."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add token auth middleware
    app.add_middleware(TokenAuthMiddleware)

    # Epic #26 P3 — SessionMiddleware powers authlib's PKCE-verifier +
    # state round-trip between /api/auth/oidc/login and /callback. The
    # secret is the JWT secret (already a strong process secret).
    # Cookie is scoped to the OIDC routes via SameSite=Lax + HTTP-only.
    from starlette.middleware.sessions import SessionMiddleware
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.JWT_SECRET,
        session_cookie="aif_oidc_session",
        max_age=600,  # 10 min — enough to complete the OIDC redirect dance
        same_site="lax",
        https_only=False,  # operator's reverse-proxy adds Secure
    )

    # Epic #26 P6 (wired in v3.0.2) — CorrelationIdMiddleware. Added
    # LAST so it's the outermost layer: it sets the X-Request-ID
    # contextvar BEFORE TokenAuth runs (so 401-rejected requests still
    # carry the ID in their response, which auditors rely on to trace
    # failed auth attempts).
    from .observability import CorrelationIdMiddleware, install_httpx_propagation
    app.add_middleware(CorrelationIdMiddleware)
    # Patch httpx clients to forward the correlation ID on outbound
    # calls. Idempotent.
    install_httpx_propagation()

    # Auth routes (prefix defined in router: /api/auth)
    app.include_router(auth_routes.router)

    # Epic #26 P3 — OIDC SSO routes (prefix defined in router: /api/auth/oidc).
    # Endpoints return 404 when APP_OIDC_ENABLED isn't set, so this is a
    # no-op for installations that haven't configured an IdP.
    from .routes import oidc_routes
    app.include_router(oidc_routes.router)

    # Organization routes (prefix defined in router: /api/orgs)
    app.include_router(organizations.router)

    # API key management (prefix defined in router: /api/keys)
    app.include_router(api_keys.router)

    # Git credentials for portal-managed clone auth (epic #82 PR-C)
    app.include_router(git_credentials.router)

    # Test-target credentials for "log in then test" (#107)
    app.include_router(test_target_credentials.router)

    # Provider runtime version manager (#121)
    app.include_router(provider_runtimes.router)
    app.include_router(cloud.router)
    app.include_router(visual_inspection.router)

    # Audit log routes (prefix defined in router: /api/orgs)
    app.include_router(audit.router)

    # Epic #26 P5.3 — /api/audit/export streaming + P5.5 GDPR erasure.
    app.include_router(audit.export_router)
    from .routes import gdpr as gdpr_routes
    app.include_router(gdpr_routes.router)

    # Notification routes (prefix defined in router: /api/notifications)
    app.include_router(notifications.router)

    # Include API routers
    app.include_router(projects.router, prefix="/api/projects", tags=["Projects"])
    app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
    # Execution routes also under /api/tasks for frontend compatibility
    app.include_router(execution.router, prefix="/api/tasks", tags=["Task Execution"])
    # PFactory portal endpoints (Task 9 / #10) — read-only over the
    # PFactory workspace filesystem at ~/.pfactory/workspaces/.
    from .routes import pfactory_tasks as pfactory_tasks_routes
    app.include_router(
        pfactory_tasks_routes.router,
        prefix="/api/pfactory/tasks",
        tags=["PFactory Tasks"],
    )

    # Plan Factory — intake channels + the planning pipeline (issues #4, #20).
    from .routes import contract_events as contract_events_routes
    from .routes import plan_intake as plan_intake_routes
    from .routes import plan_meta as plan_meta_routes
    from .routes import plan_pipeline as plan_pipeline_routes
    app.include_router(plan_intake_routes.router)
    app.include_router(plan_pipeline_routes.router)
    app.include_router(plan_meta_routes.router)
    # Inbound RFC-0001 completion events from AIFactory/TFactory (#65 child 9).
    app.include_router(contract_events_routes.router)

    app.include_router(settings_routes.router, prefix="/api/settings", tags=["Settings"])
    app.include_router(cli_accounts_routes.router, prefix="/api/settings", tags=["CLI Accounts"])
    app.include_router(llm_endpoints_routes.router)
    app.include_router(docs_targets.router)
    app.include_router(files.router, prefix="/api/files", tags=["Files"])
    app.include_router(terminal.router, prefix="/api/terminals", tags=["Terminals"])

    # Email OAuth + account management routes (prefix defined in router: /api/email)
    app.include_router(email.router)

    # GitHub routes
    app.include_router(github.router, prefix="/api/github", tags=["GitHub"])

    # Capability discovery (Epic #44 R2) — always mounted; the frontend
    # consults this on load to know whether to render the Live Agent
    # Console tab.  The router already declares its own prefix.
    app.include_router(capabilities.router, tags=["Capabilities"])
    app.include_router(search.router, tags=["Search"])
    app.include_router(mcp.router)

    # PFactory MCP server — planning-context tools over POST /mcp (epic #87 /
    # #89). JSON-RPC 2.0; Bearer PFACTORY_MCP_SECRET. Lets the Copilot cloud
    # agent (and AIFactory/TFactory) retrieve epic/requirements/decomposition/
    # task-contract/review-status during implementation.
    app.include_router(mcp_rpc.router)

    # Remote HTTP+SSE MCP server (Epic #50 / Issue #83) — opt-in via
    # PFACTORY_MCP_REMOTE_ENABLED=true.  Exposes the PFactory task
    # control plane to non-Claude MCP clients (Cursor, Continue.dev,
    # custom scripts).
    from . import mcp_remote

    if mcp_remote.is_enabled():
        from .mcp_remote.server import router as mcp_remote_router

        app.include_router(mcp_remote_router)
        logger.info("Remote MCP server enabled — mounted at /api/mcp-remote")

    # Stdio MCP control-plane proxy (Issue #154).
    # acw_-keyed proxy in front of the operations the stdio MCP exercises,
    # so enterprise installs can hand each laptop a scoped key instead of
    # the host-wide admin token. Legacy admin token still works as a wildcard
    # so v1.0 single-user deployments are unaffected — always mounted.
    from .mcp_stdio import router as mcp_stdio_router

    app.include_router(mcp_stdio_router)
    logger.info("Stdio MCP proxy mounted at /api/mcp-stdio")

    # Auto-Fix routes (multi-provider polling backing useAutoFix.ts).
    # Endpoints live under /api/projects/{id}/auto-fix/* so they match
    # the per-project nesting the frontend already follows.
    app.include_router(auto_fix.router, prefix="/api/projects", tags=["Auto-Fix"])

    # Git and utility routes
    app.include_router(git.router, prefix="/api/git", tags=["Git"])
    app.include_router(git.ollama_router, prefix="/api/ollama", tags=["Ollama"])
    app.include_router(git.claude_code_router, prefix="/api/claude-code", tags=["Claude Code"])
    app.include_router(git.mcp_router, prefix="/api/mcp", tags=["MCP"])
    app.include_router(git.updates_router, prefix="/api/updates", tags=["Updates"])

    # Memory infrastructure routes
    app.include_router(context.router, prefix="/api/memory", tags=["Memory"])

    # Logs viewing routes
    app.include_router(logs_routes.router, prefix="/api/logs", tags=["Logs"])

    # Skills knowledge base routes
    app.include_router(skills.router, prefix="/api/skills", tags=["Skills"])

    # Include WebSocket routers
    app.include_router(logs_ws.router, tags=["WebSocket"])
    app.include_router(progress_ws.router, tags=["WebSocket"])
    app.include_router(terminal_ws.router, tags=["WebSocket"])
    app.include_router(events_ws.router, tags=["WebSocket"])

    # Epic #44 R1 — rmux Live Agent Console (opt-in).  Only mount when
    # PFACTORY_RMUX_ENABLED=true; otherwise the agent-console routes
    # are 404 and the JS frontend hides the Live Console tab.  Bank
    # pilot image (WITH_RMUX=false) cannot reach this code anyway —
    # the rmux/ package imports break at module load when the
    # bundled binary isn't present.
    from .rmux import is_rmux_enabled
    if is_rmux_enabled():
        from .rmux import console_router
        app.include_router(console_router)
        logger.info("[main] rmux Live Agent Console enabled — bridge router mounted")

    # Epic #26 P6 (wired in v3.0.2) — Prometheus /metrics. Called
    # AFTER all routers are mounted so the instrumentator can derive
    # cardinality-capped `handler` labels from FastAPI's route table.
    # Optional METRICS_SCRAPE_TOKEN bearer gate is read from env at
    # install time.
    from .observability import install_metrics
    install_metrics(app)

    # Health check endpoint (no auth required)
    @app.get("/api/health")
    async def health_check():
        from .provider_health import provider_credential_health

        return {
            "status": "healthy",
            "version": app.version,
            # Provider-credential config pre-flight (#109) — booleans only, no
            # secrets; CFactory surfaces it as a provider-auth health tile.
            "provider_auth": provider_credential_health(),
        }

    # Mount static files for SPA (if build directory exists).
    #
    # Cache policy (SSO fix) — the SPA shell (index.html) MUST NOT be
    # heuristically cached by the browser, or users keep running the previous
    # build's JS after an upgrade (which is exactly how a fixed auth bug can
    # look unfixed). Starlette's StaticFiles sends ETag/Last-Modified but no
    # Cache-Control, which triggers browser heuristic caching of the shell.
    # So: HTML responses → `no-cache` (store but always revalidate; cheap 304s);
    # content-hashed assets under /assets/ → long-lived immutable cache.
    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("text/html"):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            elif "/assets/" in (scope.get("path") or ""):
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
            return response

    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="static")
    else:
        # Placeholder for development
        @app.get("/")
        async def root():
            return {
                "message": "PFactory Web Server",
                "docs": "/docs",
                "note": "Frontend not built yet. Run 'npm run build' in apps/frontend-web/",
            }

    return app


# Create the app instance
app = create_app()


# Add Bearer token auth to OpenAPI schema so Swagger UI shows the Authorize button
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    openapi_schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT access token or legacy API token",
        }
    }
    openapi_schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()

    # Build uvicorn config
    uvicorn_config = {
        "app": "server.main:app",
        "host": settings.HOST,
        "port": settings.PORT,
        "reload": settings.DEBUG,
    }

    # Add SSL if enabled
    if settings.SSL_ENABLED:
        uvicorn_config["ssl_certfile"] = settings.SSL_CERTFILE
        uvicorn_config["ssl_keyfile"] = settings.SSL_KEYFILE
        logger.info(f"HTTPS enabled with certificate: {settings.SSL_CERTFILE}")

    uvicorn.run(**uvicorn_config)
