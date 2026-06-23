import os
from dotenv import load_dotenv

load_dotenv()  # loads .env in local dev; no-op if not present (e.g. on CF)

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .api.chat import router as chat_router
from .api.health import router as health_router
from .api.tools import router as tools_router
from .api.session import router as session_router
from .api.session_history import router as session_history_router
from .tools.sap_tools import register_all_tools, aclose_client as aclose_tools_client
from .agent.memory import aclose_client as aclose_memory_client

# Register all SAP tools at startup
register_all_tools()

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Release pooled HTTP connections cleanly on shutdown.
    await aclose_tools_client()
    await aclose_memory_client()


app = FastAPI(title="SAP AI Agent", version="1.2.0", lifespan=lifespan)

# ── Rate limiting ────────────────────────────────────────────────────
# Per-IP limit. This is a coarse first line of defense — once real auth
# is in front of this service, switch the key_func to the authenticated
# principal instead of IP, since IPs are shared behind NAT/corporate proxies.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ─────────────────────────────────────────────────────────────
# In production, set ALLOWED_ORIGINS to the exact frontend origin
# (e.g. https://sd-agent-ui.cfapps.us10-001.hana.ondemand.com).
# "*" is only acceptable for local development.
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "*")
origins = ["*"] if _allowed_origins == "*" else [o.strip() for o in _allowed_origins.split(",")]
if origins == ["*"] and os.getenv("ENV", "development") == "production":
    raise RuntimeError(
        "ALLOWED_ORIGINS must be set to a specific origin in production; "
        "refusing to start with a wildcard CORS policy."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ── Generic error handler ───────────────────────────────────────────
# Prevents raw exception text (stack details, internal URLs, etc.)
# from reaching the client on unhandled errors outside the agent loop.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import logging
    logging.exception("Unhandled exception on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )


app.include_router(health_router)
app.include_router(session_router, prefix="/api")
app.include_router(session_history_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
