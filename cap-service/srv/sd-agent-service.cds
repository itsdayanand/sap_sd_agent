using { sd.agent as my } from '../db/schema';

/**
 * Service consumed by the Python FastAPI agent.
 * - ChatSessions / ChatMessages: persistence (file-based SQLite, see db/schema.cds)
 * - appendMessage: atomically assigns the next sequenceNo for a session,
 *   so concurrent requests for the same session can't collide on
 *   ordering (the Python layer no longer computes sequence numbers itself).
 * - SD actions: validated proxy calls to SAP Business Accelerator Hub
 *   sandbox OData APIs. Each action returns a uniform { ok, data, error } shape
 *   and falls back to mock fixtures if the sandbox is unreachable.
 */
service SDAgentService {

    entity ChatSessions as projection on my.ChatSessions;
    entity ChatMessages as projection on my.ChatMessages;

    // ── Persistence helper ───────────────────────────────────
    action appendMessage(
        sessionId: UUID not null,
        role: String(20) not null,
        content: LargeString not null,
        toolCalls: LargeString
    ) returns String;

    // ── Tool 1 ──────────────────────────────────────────────
    action getSalesOrders(customerId: String(10) not null, status: String(1), forceMock: Boolean) returns String;

    // ── Tool 2 ──────────────────────────────────────────────
    action getCustomerDetails(customerId: String(10) not null, forceMock: Boolean) returns String;

    // ── Tool 3 ──────────────────────────────────────────────
    action getPricingConditions(customerId: String(10) not null, materialId: String(40) not null, forceMock: Boolean) returns String;

    // ── Tool 4 ──────────────────────────────────────────────
    action getDeliveryStatus(salesOrderId: String(10) not null, forceMock: Boolean) returns String;

    // ── Tool 5 ──────────────────────────────────────────────
    action getBillingDocuments(salesOrderId: String(10) not null, forceMock: Boolean) returns String;
}
