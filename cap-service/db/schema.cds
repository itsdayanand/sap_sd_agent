namespace sd.agent;

using { cuid, managed } from '@sap/cds/common';

/**
 * One chat session per browser tab / user conversation.
 * Persisted to a file-based SQLite DB (db/sd-agent.sqlite) in both
 * local dev and production. HANA Cloud is deliberately NOT used here —
 * on a BTP trial account, HANA Cloud trial entitlement/availability is
 * unreliable, and this app's persistence needs don't warrant it anyway.
 * If you outgrow SQLite (multi-instance scaling, real concurrent load),
 * switching back to HANA only requires changing package.json's
 * cds.requires.db block and re-running `cds deploy --to hana` — the
 * schema and service code do not need to change.
 */
entity ChatSessions : cuid, managed {
    label    : String(100);          // optional friendly name, e.g. first user message
    messages : Composition of many ChatMessages on messages.session = $self;
}

/**
 * Individual turns in a session. Stored so the agent can
 * reload history after a server restart or page refresh.
 */
entity ChatMessages : cuid, managed {
    session    : Association to ChatSessions;
    role       : String(20);          // 'user' | 'assistant' | 'tool'
    content    : LargeString;
    toolCalls  : LargeString;         // JSON-stringified tool call log, nullable
    sequenceNo : Integer;             // ordering within a session
}
