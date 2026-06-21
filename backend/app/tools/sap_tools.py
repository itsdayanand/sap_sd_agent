import os
import json as _json
import logging
import httpx
from .base import SAPTool
from .registry import registry

logger = logging.getLogger(__name__)

# Base URL of the CAP service (this replaces direct OData/sandbox calls).
# Locally this is http://localhost:4004/odata/v4/sd-agent
# On BTP CF this is the CAP app's route, e.g.
#   https://sd-agent-cap-service.cfapps.us10-001.hana.ondemand.com/odata/v4/sd-agent
CAP_BASE_URL = os.getenv("CAP_BASE_URL", "http://localhost:4004/odata/v4/sd-agent")
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
    if isinstance(data, list) and len(data) == 0:
        return {"message": empty_message, "data": []}
    return {"source": result.get("source", "sandbox"), "data": data}


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
            "Status codes: A=Not Started, B=Partial, C=Completed."
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

    async def execute(self, customerId: str, status: str = None, **kwargs) -> dict:
        result = await call_cap_action("getSalesOrders", {"customerId": customerId, "status": status})
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

    async def execute(self, customerId: str, **kwargs) -> dict:
        result = await call_cap_action("getCustomerDetails", {"customerId": customerId})
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

    async def execute(self, customerId: str, materialId: str, **kwargs) -> dict:
        result = await call_cap_action(
            "getPricingConditions", {"customerId": customerId, "materialId": materialId}
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

    async def execute(self, salesOrderId: str, **kwargs) -> dict:
        result = await call_cap_action("getDeliveryStatus", {"salesOrderId": salesOrderId})
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

    async def execute(self, salesOrderId: str, **kwargs) -> dict:
        result = await call_cap_action("getBillingDocuments", {"salesOrderId": salesOrderId})
        return _humanize(result, f"No billing documents found for sales order {salesOrderId}.")


def register_all_tools():
    registry.register(GetSalesOrdersTool())
    registry.register(GetCustomerDetailsTool())
    registry.register(GetPricingConditionsTool())
    registry.register(GetDeliveryStatusTool())
    registry.register(GetBillingDocumentsTool())
