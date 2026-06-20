const axios = require('axios');

const SANDBOX_BASE = process.env.SANDBOX_BASE_URL
    || 'https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap';
const SANDBOX_API_KEY = process.env.SANDBOX_API_KEY || '';

// FORCE_MOCK: when true, every tool call returns fixture data immediately,
// regardless of whether a sandbox key is configured. Use this to demo
// safely without touching the sandbox at all.
const FORCE_MOCK = process.env.FORCE_MOCK === 'true';

// FALLBACK_ON_FAILURE: when the live sandbox call throws (network error,
// auth failure, timeout), return fixture data instead of an error so a
// live demo doesn't hard-fail mid-conversation. This is independent of
// FORCE_MOCK — it only kicks in if a real sandbox call was attempted and
// failed.
const FALLBACK_ON_FAILURE = process.env.FALLBACK_ON_FAILURE !== 'false';

// Reused HTTP client with keep-alive instead of a bare axios.get per call,
// so repeated sandbox calls in the same process reuse TCP/TLS connections.
const http = axios.create({
    timeout: 30000,
    headers: { APIKey: SANDBOX_API_KEY, Accept: 'application/json' },
});

/**
 * Guardrails on inputs before they ever reach the sandbox.
 * CAP layer does real validation work here — not just a pass-through.
 * All patterns are allow-lists (only known-safe characters pass), which
 * is what prevents these values from breaking out of the single-quoted
 * OData $filter literals they get interpolated into downstream.
 */
function validateCustomerId(customerId) {
    if (!customerId || typeof customerId !== 'string') {
        throw { code: 400, message: 'customerId is required.' };
    }
    if (!/^[0-9]{1,10}$/.test(customerId)) {
        throw { code: 400, message: `customerId '${customerId}' is invalid. Expected up to 10 numeric digits.` };
    }
}

function validateSalesOrderId(salesOrderId) {
    if (!salesOrderId || typeof salesOrderId !== 'string') {
        throw { code: 400, message: 'salesOrderId is required.' };
    }
    if (!/^[0-9]{1,10}$/.test(salesOrderId)) {
        throw { code: 400, message: `salesOrderId '${salesOrderId}' is invalid. Expected up to 10 numeric digits.` };
    }
}

function validateMaterialId(materialId) {
    if (!materialId || typeof materialId !== 'string') {
        throw { code: 400, message: 'materialId is required.' };
    }
    // Previously this only checked length (<=40 chars), which let values
    // like "' or 1 eq 1 or Material eq '" through into the $filter string
    // built in sd-agent-service.js — a real OData injection vector.
    // SAP material numbers are alphanumeric (plus - and _ in many
    // configs); this allow-list closes that gap.
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(materialId)) {
        throw {
            code: 400,
            message: `materialId '${materialId}' is invalid. Expected up to 40 alphanumeric characters (- and _ allowed).`,
        };
    }
}

function validateStatus(status) {
    if (status && !['A', 'B', 'C'].includes(status)) {
        throw { code: 400, message: `status '${status}' is invalid. Must be one of A, B, C.` };
    }
}

/**
 * Calls the SAP Business Accelerator Hub sandbox.
 *
 * - FORCE_MOCK=true            -> always returns fixture data, no network call.
 * - no SANDBOX_API_KEY set     -> always returns fixture data (can't call sandbox anyway).
 * - real call fails AND
 *   FALLBACK_ON_FAILURE=true   -> returns fixture data, clearly flagged as a fallback.
 * - real call fails AND
 *   FALLBACK_ON_FAILURE=false  -> returns { ok: false, error }.
 */
async function callSandbox(path, params, mockFixture) {
    if (FORCE_MOCK || !SANDBOX_API_KEY) {
        return { ok: true, source: 'mock', data: mockFixture };
    }
    try {
        const res = await http.get(`${SANDBOX_BASE}/${path}`, {
            params: { '$format': 'json', ...params },
        });
        const body = res.data?.d?.results ?? res.data?.d ?? res.data;
        return { ok: true, source: 'sandbox', data: body };
   } catch (err) {
    console.error('[sandbox-client] Sandbox call FAILED:', {
        url: `${SANDBOX_BASE}/${path}`,
        status: err.response?.status,
        statusText: err.response?.statusText,
        responseData: JSON.stringify(err.response?.data)?.slice(0, 500),
        message: err.message,
    });

    if (FALLBACK_ON_FAILURE) {
        return {
            ok: true,
            source: 'mock-fallback',
            note: 'This is demo/sandbox fixture data, not a live SAP record. The lookup itself succeeded.',
            data: mockFixture,
        };
    }
    return { ok: false, error: 'Sandbox call failed.' };
    }
}

module.exports = {
    validateCustomerId,
    validateSalesOrderId,
    validateMaterialId,
    validateStatus,
    callSandbox,
};
