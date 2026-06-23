import uuid
from fastapi import APIRouter, HTTPException
from ..agent import memory

router = APIRouter()


def _is_valid_session_id(session_id: str) -> bool:
    try:
        uuid.UUID(session_id)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@router.get("/session/{session_id}/history")
async def get_session_history(session_id: str):
    """
    Returns this session's saved turns from CAP/SQLite, so the frontend
    can actually load an old conversation's content when the person
    clicks it in the sidebar — previously, switching sessions only
    cleared the screen and waited for the next message to (re-)load
    history server-side, so clicking an old chat showed nothing until
    you sent a new message in it.
    """
    if not _is_valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format.")

    history = await memory.load_history(session_id)
    return {"session_id": session_id, "messages": history}
