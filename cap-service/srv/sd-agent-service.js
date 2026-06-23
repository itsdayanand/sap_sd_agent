const cds = require('@sap/cds');
const {
    validateCustomerId,
    validateSalesOrderId,
    validateMaterialId,
    validateStatus,
    callSandbox
} = require('./lib/sandbox-client');
const fixtures = require('./lib/mock-fixtures');

module.exports = cds.service.impl(async function () {
    const { ChatMessages } = this.entities;

    // ── Persistence helper: atomic sequence number assignment ─────────
    this.on('appendMessage', async (req) => {
        const { sessionId, role, content, toolCalls } = req.data;

        if (!['user', 'assistant', 'tool'].includes(role)) {
            return JSON.stringify({ ok: false, error: `Invalid role '${role}'.` });
        }

        try {
            // Run the read-max + insert inside a single transaction so two
            // concurrent appendMessage calls for the same session can't both
            // read the same MAX(sequenceNo) and collide on the same value.
            const tx = cds.transaction(req);
            const existing = await tx.run(
                SELECT.one`max(sequenceNo) as maxSeq`.from(ChatMessages).where({ session_ID: sessionId })
            );
            const nextSeq = (existing?.maxSeq ?? -1) + 1;

            await tx.run(
                INSERT.into(ChatMessages).entries({
                    session_ID: sessionId,
                    role,
                    content,
                    toolCalls: toolCalls || '[]',
                    sequenceNo: nextSeq,
                })
            );

            return JSON.stringify({ ok: true, sequenceNo: nextSeq });
        } catch (e) {
            return JSON.stringify({ ok: false, error: e.message });
        }
    });

    // ── Tool 1: Sales Orders ───────────────────────────────────────
    this.on('getSalesOrders', async (req) => {
        const { customerId, status, forceMock } = req.data;
        try {
            validateCustomerId(customerId);
            validateStatus(status);
        } catch (e) {
            return JSON.stringify({ ok: false, error: e.message });
        }

        const filters = [`SoldToParty eq '${customerId}'`];
        if (status) filters.push(`OverallSDProcessStatus eq '${status}'`);

        const result = await callSandbox(
            'API_SALES_ORDER_SRV/A_SalesOrder',
            {
                '$filter': filters.join(' and '),
                '$select': 'SalesOrder,SoldToParty,SalesOrderType,OverallSDProcessStatus,CreationDate,TotalNetAmount,TransactionCurrency',
                '$top': '10',
                '$orderby': 'CreationDate desc',
                '$inlinecount': 'allpages'
            },
            fixtures.salesOrders(customerId, status),
            forceMock,
            fixtures.MOCK_TOTAL_ORDER_COUNT
        );

        return JSON.stringify(result);
    });

    // ── Tool 2: Customer Details ───────────────────────────────────
    this.on('getCustomerDetails', async (req) => {
        const { customerId, forceMock } = req.data;
        try {
            validateCustomerId(customerId);
        } catch (e) {
            return JSON.stringify({ ok: false, error: e.message });
        }

        const result = await callSandbox(
            `API_BUSINESS_PARTNER/A_BusinessPartner('${customerId}')`,
            { '$select': 'BusinessPartner,BusinessPartnerFullName,BusinessPartnerType,BusinessPartnerIsBlocked' },
            fixtures.customerDetails(customerId),
            forceMock
        );

        return JSON.stringify(result);
    });

    // ── Tool 3: Pricing Conditions ──────────────────────────────────
    this.on('getPricingConditions', async (req) => {
        const { customerId, materialId, forceMock } = req.data;
        try {
            validateCustomerId(customerId);
            validateMaterialId(materialId);
        } catch (e) {
            return JSON.stringify({ ok: false, error: e.message });
        }

        const result = await callSandbox(
            'API_SLSPRICINGCONDITIONRECORD_SRV/A_SlsPricingConditionRecord',
            {
                '$filter': `Customer eq '${customerId}' and Material eq '${materialId}'`,
                '$select': 'ConditionType,ConditionRateValue,ConditionCurrency,Customer,Material',
                '$top': '10'
            },
            fixtures.pricingConditions(customerId, materialId),
            forceMock
        );

        return JSON.stringify(result);
    });

    // ── Tool 4: Delivery Status ──────────────────────────────────────
    this.on('getDeliveryStatus', async (req) => {
        const { salesOrderId, forceMock } = req.data;
        try {
            validateSalesOrderId(salesOrderId);
        } catch (e) {
            return JSON.stringify({ ok: false, error: e.message });
        }

        const result = await callSandbox(
            'API_OUTBOUND_DELIVERY_SRV;v=0002/A_OutbDeliveryHeader',
            {
                '$filter': `SalesOrder eq '${salesOrderId}'`,
                '$select': 'DeliveryDocument,SalesOrder,OverallDeliveryStatus,GoodsIssueTime,ShippingPoint',
                '$top': '5'
            },
            fixtures.deliveryStatus(salesOrderId),
            forceMock
        );

        return JSON.stringify(result);
    });

    // ── Tool 5: Billing Documents ─────────────────────────────────────
    this.on('getBillingDocuments', async (req) => {
        const { salesOrderId, forceMock } = req.data;
        try {
            validateSalesOrderId(salesOrderId);
        } catch (e) {
            return JSON.stringify({ ok: false, error: e.message });
        }

        const result = await callSandbox(
            'API_BILLING_DOCUMENT_SRV/A_BillingDocument',
            {
                '$filter': `SalesOrder eq '${salesOrderId}'`,
                '$select': 'BillingDocument,SalesOrder,BillingDocumentDate,NetAmount,TransactionCurrency,PaymentStatus',
                '$top': '5'
            },
            fixtures.billingDocuments(salesOrderId),
            forceMock
        );

        return JSON.stringify(result);
    });
});
