import os
import json as _json
import logging
import httpx
from .base import SAPTool
from .registry import registry

logger = logging.getLogger(__name__)

# Base URL of the CAP service (this replaces direct OData/sandbox calls).
# Locally this is http://localhost:4004/odata/v4/sdagent
# On BTP CF this is the CAP app's route, e.g.
#   https://sd-agent-cap-service.cfapps.us10-001.hana.ondemand.com/odata/v4/sdagent
CAP_BASE_URL = os.getenv("CAP_BASE_URL", "http://localhost:4004/odata/v4/sdagent")
CAP_TIMEOUT_SECONDS = float(os.getenv("CAP_TIMEOUT_SECONDS", "35.0"))

# Single shared, connection-pooled client reused across all tool calls
# instead of opening a new TCP/TLS connection per request.
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


async def call_cap_action(action: str, payload: dict) -> dict:
    """
    Calls a CAP action (POST .../actionName) and parses the
    JSON-stringified result the CAP service returns.
    Never raises — always returns a dict the agent loop can hand to the LLM.
    Error messages returned here are generic by design: this dict can end
    up echoed into the LLM's answer to the end user, so raw exception text
    (which can include internal hostnames/ports) is logged, not returned.
    """
    url = f"{CAP_BASE_URL}/{action}"
    client = _get_client()
    try:
        res = await client.post(url, json=payload)
        res.raise_for_status()
        body = res.json()
        # CAP actions here return a JSON string (see srv/sd-agent-service.js);
        # OData wraps the action's scalar return in a "value" key.
        raw = body.get("value", body)
        if isinstance(raw, str):
            return _json.loads(raw)
        return raw
    except httpx.TimeoutException:
        logger.warning("CAP service timed out calling '%s'", action)
        return {"ok": False, "error": f"The '{action}' lookup timed out. Please try again."}
    except httpx.HTTPStatusError as e:
        logger.warning("CAP service returned %s for '%s'", e.response.status_code, action)
        return {"ok": False, "error": f"The '{action}' lookup failed. Please try again."}
    except Exception:
        logger.exception("Unexpected error calling CAP action '%s'", action)
        return {"ok": False, "error": f"The '{action}' lookup failed unexpectedly. Please try again."}



def _humanize(result: dict, empty_message: str) -> dict:
    """Turn a raw CAP/sandbox result into a business-friendly shape."""
    if not result.get("ok"):
        return {"error": result.get("error", "Unknown error"), "message": empty_message}
    data = result.get("data")
    total_count = result.get("totalCount")
    if isinstance(data, list) and len(data) == 0:
        # Preserve "source" even on an empty result — without this, an
        # empty mock-fallback result would look indistinguishable from an
        # empty real-sandbox result, and the turn-level mock-consistency
        # tracking in loop.py wouldn't detect that this call used mock data.
        empty_result = {"message": empty_message, "data": [], "source": result.get("source", "sandbox")}
        if result.get("note"):
            empty_result["note"] = result["note"]
        if result.get("fallbackReason"):
            empty_result["fallbackReason"] = result["fallbackReason"]
        return empty_result

    humanized = {"source": result.get("source", "sandbox"), "data": data}

    # Preserve these so the LLM can tell a deliberate Mock-mode selection
    # (source: "mock", no fallbackReason) apart from a genuine Live-mode
    # failure that fell back automatically (source: "mock-fallback" WITH
    # fallbackReason "timeout"/"unreachable") — see system_prompt.py rule 8.
    # Without this, both cases looked identical to the LLM, and a real
    # sandbox outage in Live mode would silently present as ordinary mock
    # data with no warning to the user.
    if result.get("note"):
        humanized["note"] = result["note"]
    if result.get("fallbackReason"):
        humanized["fallbackReason"] = result["fallbackReason"]

    # totalCount comes from OData's $inlinecount=allpages — the TRUE number
    # of matching rows, independent of how many were actually returned
    # (capped by $top). When totalCount exceeds what's in "data", the
    # caller only saw a partial page — surface that explicitly so the LLM
    # doesn't present a partial result as if it were everything. Without
    # this flag, a customer with thousands of orders would silently look
    # identical to one with exactly 10.
    if isinstance(data, list) and isinstance(total_count, int) and total_count > len(data):
        humanized["totalAvailable"] = total_count
        humanized["returnedCount"] = len(data)
        humanized["truncated"] = True

    return humanized


# ── Tool 1: Sales Orders ─────────────────────────────────────────────
class GetSalesOrdersTool(SAPTool):
    @property
    def name(self) -> str:
        return "get_sales_orders"

    @property
    def description(self) -> str:
        return (
            "Retrieve sales orders from SAP S/4HANA for a given customer. "
            "Returns order ID, status, amount, and creation date. "
            "Status codes: A=Not Started, B=Partial, C=Completed. "
            "Returns at most 10 orders per call, sorted newest first. If "
            "the customer has more than 10 matching orders, the result "
            "includes 'truncated': true, 'returnedCount', and "
            "'totalAvailable' — when present, you MUST tell the user only "
            "a partial set was retrieved (e.g. 'showing the 10 most recent "
            "of 10,000 total orders') rather than presenting the 10 shown "
            "as if they were the complete order history."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "customerId": {"type": "string", "description": "SAP sold-to party number, e.g. '10100001'"},
                "status": {"type": "string", "description": "Filter by SD process status", "enum": ["A", "B", "C"]},
            },
            "required": ["customerId"],
        }

    async def execute(self, customerId: str, status: str = None, force_mock: bool = False, **kwargs) -> dict:
        result = await call_cap_action(
            "getSalesOrders", {"customerId": customerId, "status": status, "forceMock": force_mock}
        )
        return _humanize(result, f"No open sales orders found for customer {customerId}.")


# ── Tool 2: Customer Details ─────────────────────────────────────────
class GetCustomerDetailsTool(SAPTool):
    @property
    def name(self) -> str:
        return "get_customer_details"

    @property
    def description(self) -> str:
        return (
            "Retrieve customer master data from SAP Business Partner API. "
            "Returns name, type, and block status. "
            "Always call this first to validate a customer exists."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "customerId": {"type": "string", "description": "SAP Business Partner number, e.g. '10100001'"},
            },
            "required": ["customerId"],
        }

    async def execute(self, customerId: str, force_mock: bool = False, **kwargs) -> dict:
        result = await call_cap_action("getCustomerDetails", {"customerId": customerId, "forceMock": force_mock})
        return _humanize(result, f"Customer {customerId} was not found in the sandbox dataset.")


# ── Tool 3: Pricing Conditions ───────────────────────────────────────
class GetPricingConditionsTool(SAPTool):
    @property
    def name(self) -> str:
        return "get_pricing_conditions"

    @property
    def description(self) -> str:
        return (
            "Retrieve applicable pricing conditions and discounts for a "
            "customer/material combination. Use this to explain pricing "
            "or discount logic to the user."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "customerId": {"type": "string", "description": "SAP sold-to party number"},
                "materialId": {"type": "string", "description": "SAP material number"},
            },
            "required": ["customerId", "materialId"],
        }

    async def execute(self, customerId: str, materialId: str, force_mock: bool = False, **kwargs) -> dict:
        result = await call_cap_action(
            "getPricingConditions",
            {"customerId": customerId, "materialId": materialId, "forceMock": force_mock},
        )
        return _humanize(
            result,
            f"No pricing conditions found for customer {customerId} / material {materialId}.",
        )


# ── Tool 4: Delivery Status ──────────────────────────────────────────
class GetDeliveryStatusTool(SAPTool):
    @property
    def name(self) -> str:
        return "get_delivery_status"

    @property
    def description(self) -> str:
        return (
            "Retrieve outbound delivery documents for a sales order. "
            "Returns delivery ID, status, and goods issue time. "
            "Use to check if goods have shipped."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "salesOrderId": {"type": "string", "description": "SAP Sales Order number, e.g. '10'"},
            },
            "required": ["salesOrderId"],
        }

    async def execute(self, salesOrderId: str, force_mock: bool = False, **kwargs) -> dict:
        result = await call_cap_action(
            "getDeliveryStatus", {"salesOrderId": salesOrderId, "forceMock": force_mock}
        )
        return _humanize(result, f"No delivery documents found for sales order {salesOrderId}.")


# ── Tool 5: Billing Documents ────────────────────────────────────────
class GetBillingDocumentsTool(SAPTool):
    @property
    def name(self) -> str:
        return "get_billing_documents"

    @property
    def description(self) -> str:
        return (
            "Retrieve billing documents / invoices linked to a sales order, "
            "and their payment status. Use to check if an order has been invoiced."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "salesOrderId": {"type": "string", "description": "SAP Sales Order number, e.g. '10'"},
            },
            "required": ["salesOrderId"],
        }

    async def execute(self, salesOrderId: str, force_mock: bool = False, **kwargs) -> dict:
        result = await call_cap_action(
            "getBillingDocuments", {"salesOrderId": salesOrderId, "forceMock": force_mock}
        )
        return _humanize(result, f"No billing documents found for sales order {salesOrderId}.")


def register_all_tools():
    registry.register(GetSalesOrdersTool())
    registry.register(GetCustomerDetailsTool())
    registry.register(GetPricingConditionsTool())
    registry.register(GetDeliveryStatusTool())
    registry.register(GetBillingDocumentsTool())
