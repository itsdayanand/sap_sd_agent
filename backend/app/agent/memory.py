import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

CAP_BASE_URL = os.getenv("CAP_BASE_URL", "http://localhost:4004/odata/v4/sd-agent")
CAP_TIMEOUT_SECONDS = float(os.getenv("CAP_TIMEOUT_SECONDS", "35.0"))

# Single shared client, reused across requests instead of opening a new
# TCP/TLS connection on every call. httpx.AsyncClient is safe to share
# across concurrent async tasks.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=CAP_TIMEOUT_SECONDS)
    return _client


async def aclose_client() -> None:
    """Call on application shutdown to release the pooled connection."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _escape_odata_literal(value: str) -> str:
    """
    OData string literals escape a single quote by doubling it.
    This does NOT make raw string interpolation into $filter generally
    safe, but it is the documented OData escaping rule and prevents the
    most common breakage/injection vector for this specific literal
    position. session_id is additionally validated as a UUID by the
    /api/chat route before it ever reaches this module — defense in
    depth, not the only control.
    """
    return value.replace("'", "''")


async def ensure_session(session_id: str) -> str:
    """
    Ensures a ChatSessions row exists for this session_id.
    CAP's cuid mixin generates its own UUID key, so we store the
    frontend's session_id as the `label` and look sessions up by it,
    keeping the frontend decoupled from CAP's internal key generation.
    Returns the CAP-side session key (a cuid) to use for ChatMessages.

    session_id is validated as a UUID by the caller (api/chat.py) before
    reaching here, which is the primary control against OData filter
    injection; _escape_odata_literal is a secondary defense.
    """
    client = _get_client()
    safe_id = _escape_odata_literal(session_id)

    res = await client.get(
        f"{CAP_BASE_URL}/ChatSessions",
        params={"$filter": f"label eq '{safe_id}'", "$top": "1"},
    )
    res.raise_for_status()
    rows = res.json().get("value", [])
    if rows:
        return rows[0]["ID"]

    create_res = await client.post(
        f"{CAP_BASE_URL}/ChatSessions",
        json={"label": session_id},
    )
    create_res.raise_for_status()
    return create_res.json()["ID"]


async def save_message(cap_session_key: str, role: str, content: str, tool_calls: list = None) -> None:
    """
    Persist a single chat turn to HANA via the CAP ChatMessages entity.

    Sequence numbering is delegated to CAP (see srv/sd-agent-service.js,
    action `appendMessage`), which computes MAX(sequenceNo)+1 inside a
    single DB round-trip. This avoids the race condition where two
    concurrent requests for the same session both compute the same
    "next" sequence number on the Python side and collide.
    """
    client = _get_client()
    try:
        await client.post(
            f"{CAP_BASE_URL}/appendMessage",
            json={
                "sessionId": cap_session_key,
                "role": role,
                "content": content,
                "toolCalls": json.dumps(tool_calls or []),
            },
        )
    except Exception:
        logger.warning("Failed to persist message for session %s", cap_session_key)
        raise


async def load_history(session_id: str) -> list:
    """
    Loads prior turns for this session_id (by label), ordered by sequenceNo,
    formatted as the {role, content} list the OpenAI API expects.
    Returns [] if the session doesn't exist yet or CAP is unreachable
    (history loss is non-fatal — the agent still works, just without memory).
    """
    try:
        client = _get_client()
        safe_id = _escape_odata_literal(session_id)

        sess_res = await client.get(
            f"{CAP_BASE_URL}/ChatSessions",
            params={"$filter": f"label eq '{safe_id}'", "$top": "1"},
        )
        sess_res.raise_for_status()
        rows = sess_res.json().get("value", [])
        if not rows:
            return []
        cap_key = rows[0]["ID"]

        msg_res = await client.get(
            f"{CAP_BASE_URL}/ChatMessages",
            params={
                "$filter": f"session_ID eq {cap_key}",
                "$orderby": "sequenceNo asc",
            },
        )
        msg_res.raise_for_status()
        msgs = msg_res.json().get("value", [])
        return [
            {"role": m["role"], "content": m["content"]}
            for m in msgs
            if m["role"] in ("user", "assistant")
        ]
    except Exception:
        logger.warning("Failed to load history for session %s (CAP/HANA unreachable?)", session_id)
        return []
