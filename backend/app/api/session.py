import uuid
from fastapi import APIRouter

router = APIRouter()


@router.post("/session/new")
async def new_session():
    """
    Issues a new, cryptographically random session ID.
    The frontend must call this once per new conversation and use the
    returned id on subsequent /api/chat calls — it must never invent its
    own session id (e.g. from Date.now()), since that id is the only
    thing standing between one user's chat history and another's.
    """
    return {"session_id": str(uuid.uuid4())}
