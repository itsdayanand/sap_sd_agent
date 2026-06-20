from fastapi import APIRouter
import os
import httpx

router = APIRouter()


@router.get("/health")
async def health():
    """
    Basic liveness check, plus a best-effort CAP connectivity check so
    deployment issues are visible immediately rather than surfacing as
    a confusing agent error mid-demo.
    """
    cap_base = os.getenv("CAP_BASE_URL", "http://localhost:4004/odata/v4/sd-agent")
    cap_status = "unknown"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{cap_base}/$metadata")
            cap_status = "reachable" if res.status_code < 500 else "error"
    except Exception:
        cap_status = "unreachable"

    return {"status": "ok", "service": "SAP AI Agent", "cap_service": cap_status}
