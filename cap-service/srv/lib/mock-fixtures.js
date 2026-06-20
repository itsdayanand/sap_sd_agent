// Fixture data shaped to mirror real SAP Business Accelerator Hub
// sandbox responses, so a demo audience sees realistic-looking output
// even if the sandbox API is rate-limited or unreachable.

const salesOrders = (customerId, status) => [
    {
        SalesOrder: '10', SoldToParty: customerId, SalesOrderType: 'OR',
        OverallSDProcessStatus: status || 'B', CreationDate: '2026-05-12',
        TotalNetAmount: '12500.00', TransactionCurrency: 'EUR'
    },
    {
        SalesOrder: '11', SoldToParty: customerId, SalesOrderType: 'OR',
        OverallSDProcessStatus: status || 'A', CreationDate: '2026-06-02',
        TotalNetAmount: '4300.00', TransactionCurrency: 'EUR'
    }
];

const customerDetails = (customerId) => ({
    BusinessPartner: customerId,
    BusinessPartnerFullName: 'Mock Customer GmbH',
    BusinessPartnerType: '2',
    BusinessPartnerIsBlocked: false
});

const pricingConditions = (customerId, materialId) => [
    {
        ConditionType: 'PR00', ConditionRateValue: '125.00',
        ConditionCurrency: 'EUR', Customer: customerId, Material: materialId
    },
    {
        ConditionType: 'K004', ConditionRateValue: '-5.00',
        ConditionCurrency: 'EUR', Customer: customerId, Material: materialId,
        Description: 'Volume discount'
    }
];

const deliveryStatus = (salesOrderId) => [
    {
        DeliveryDocument: '80001', SalesOrder: salesOrderId,
        OverallDeliveryStatus: 'C', GoodsIssueTime: '2026-06-10T09:30:00Z',
        ShippingPoint: 'WH01'
    }
];

const billingDocuments = (salesOrderId) => [
    {
        BillingDocument: '90001', SalesOrder: salesOrderId,
        BillingDocumentDate: '2026-06-11', NetAmount: '12500.00',
        TransactionCurrency: 'EUR', PaymentStatus: 'Open'
    }
];

module.exports = {
    salesOrders,
    customerDetails,
    pricingConditions,
    deliveryStatus,
    billingDocuments
};
