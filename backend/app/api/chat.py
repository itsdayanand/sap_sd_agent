import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..agent.loop import run_agent
from ..models.schemas import ChatRequest

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


def _is_valid_session_id(session_id: str) -> bool:
    """
    Session ids must be server-issued UUIDs (see /api/session/new).
    Rejecting anything else closes off the IDOR risk of a client
    supplying an arbitrary/guessable string to read another session's
    history, and also prevents malformed values reaching the OData
    $filter built in agent/memory.py.
    """
    try:
        uuid.UUID(session_id)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@router.post("/chat")
@limiter.limit("20/minute")
async def chat(req: ChatRequest, request: Request):
    """
    Main chat endpoint.
    Accepts a user message + optional client-side history.
    Returns a Server-Sent Events (SSE) stream of tokens, tool calls, and
    a final 'done' event.

    session_id must be a UUID previously issued by POST /api/session/new.
    Rate-limited per-IP to protect OpenAI/CAP/sandbox quota from abuse.
    """
    if not req.session_id or not _is_valid_session_id(req.session_id):
        raise HTTPException(
            status_code=400,
            detail="A valid session_id is required. Call POST /api/session/new first.",
        )

    history = [turn.model_dump() for turn in req.history]

    return StreamingResponse(
        run_agent(req.message, history, req.session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
