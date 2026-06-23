# SD Order Intelligence Agent

**Two deployable services**, deliberately reduced from three, for reliable
deployment on an SAP BTP **trial** account:

```
cap-service/   Node.js CAP — OData proxy + validation + SQLite persistence
               + serves the static chat UI directly (app/sd-agent-ui/)
backend/       FastAPI — agent loop, OpenAI tool-calling, SSE streaming
```

Data flow: **Browser (static UI, served by CAP) → FastAPI (agent/LLM logic) → CAP (validation + OData/mock) → SAP sandbox**

## Why this differs from a "standard" build — and why that's intentional

This started as a 3-service build (CAP + FastAPI + a separate React/Vite
frontend) with HANA Cloud for persistence. Both of those were re-thought
specifically for BTP **trial** deployment, where:

- **Trial CF org memory quota is small and inconsistently allocated**
  (historically 1–2GB total across *all* apps in the org, with documented,
  recurring community reports of trial orgs temporarily getting 0MB due to
  entitlement issues). Three apps (CAP + FastAPI + a frontend) eat into that
  quota three times over. Cutting to two apps gives meaningfully more headroom.
- **HANA Cloud trial availability is fragile** — it has been reported
  unavailable entirely in some trial regions, and even when available it's a
  small shared instance not worth the entitlement cost for "persist some chat
  turns." This app never needed HANA's actual strengths (concurrent
  multi-user write load, complex analytics) — file-based SQLite does the job
  with zero extra entitlement risk.
- **A build-tooled frontend (React/Vite) is an extra thing that can fail**
  in a toolchain you may not have deep experience debugging. The UI here is
  one plain HTML file with vanilla JS — no `npm run build`, no bundler, no
  TypeScript compiler — served directly by the CAP app as a static resource.
  It does the same things (SSE streaming, tool-call transparency, session
  history, copy/clear) with nothing that can break at build time.

**If you don't have these constraints** (a paid/enterprise BTP account with
real memory headroom, genuine HANA needs, or frontend expertise on the team),
re-introducing HANA or a framework-based frontend is a reasonable choice —
the CAP and FastAPI service code doesn't need to change either way. The
schema is written so that switching `db` back to HANA in `package.json` is
the only change required (see the comment in `db/schema.cds`).

---

## 1. CAP service (+ static UI)

```bash
cd cap-service
npm install
cp .env.example .env        # fill in SANDBOX_API_KEY if you have one
npm run watch                # starts on http://localhost:4004
```

- With `FORCE_MOCK=true` (the default), every tool action returns realistic
  mock data immediately — safe for a live demo with no sandbox dependency
  at all. Set `FORCE_MOCK=false` once you have a real `SANDBOX_API_KEY`;
  `FALLBACK_ON_FAILURE` still protects you if that call fails mid-demo.
- Persistence is a file-based SQLite DB (`db/sd-agent.sqlite`), created
  automatically on first run. No HANA Cloud service binding, no extra
  entitlement needed.
- The chat UI is served automatically at `http://localhost:4004/sd-agent-ui/`
  once the CAP app is running (CAP's default static-serving convention for
  anything under `app/<folder>/`).
- Deploy to BTP Cloud Foundry:
  ```bash
  cf push -f manifest.yml --var sandbox_api_key=YOUR_KEY
  ```
  Note the assigned route after this — you'll need it for the FastAPI
  backend's `ALLOWED_ORIGINS` and the UI's agent URL (see below).

**Note on `better-sqlite3`:** this needs a native build step (`node-gyp`) the
first time you `npm install`, which needs normal internet access to
`nodejs.org`. This works fine on a normal dev machine or BTP's buildpack —
it only fails in network-locked-down environments (e.g. some CI sandboxes).

**`better-sqlite3` alone is not enough — `@cap-js/sqlite` must also be a
dependency.** `package.json`'s `cds.requires.db.kind` is set to `"sqlite"`.
In CDS 7.x, that `kind` is resolved by the **`@cap-js/sqlite`** plugin
package, which uses `better-sqlite3` internally as its native driver —
`better-sqlite3` by itself does not register a CDS database service
implementation. If `@cap-js/sqlite` is missing from `dependencies`, CDS
falls back to trying to `require('sqlite3')` (a different, older package
that was never installed), and the app crashes at startup with
`Cannot find module 'sqlite3'`. `package.json` must list both:
```json
"dependencies": {
  "better-sqlite3": "^11",
  "@cap-js/sqlite": "^1"
}
```

## 2. FastAPI backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # or your preferred env tool
pip install -r requirements.txt
cp .env.example .env          # set OPENAI_API_KEY, point CAP_BASE_URL at the CAP service
uvicorn app.main:app --reload --port 8000
```

- `CAP_BASE_URL` must point at the CAP service's OData endpoint — locally
  this is `http://localhost:4004/odata/v4/sd-agent`.
- Check `GET http://localhost:8000/health` — it reports whether it can reach CAP.
- Deploy to BTP CF: `cf push -f manifest.yml --var openai_api_key=YOUR_KEY`
  — then update `manifest.yml`'s `CAP_BASE_URL` and `ALLOWED_ORIGINS` to
  match the actual routes CF assigns to both apps (the placeholder
  `us10-001` subdomain is a guess; your real route will be shown after
  `cf push`).

## 3. Pointing the UI at your deployed FastAPI backend

The static UI defaults to `http://localhost:8000`. For a deployed CAP app,
either:

- **Quick test**: open the deployed UI with `?agent=https://your-fastapi-route...`
  appended to the URL, or
- **Permanent fix**: edit `cap-service/app/sd-agent-ui/index.html`, find the
  line `|| 'http://localhost:8000';` near the top of the `<script>` block,
  and replace it with your deployed FastAPI route. Redeploy the CAP app
  (`cf push`) to pick up the change.

---

## ⚠️ Critical gotcha: OData `$filter` on GUID-typed fields needs a bare, unquoted literal

This one caused a very long debugging session (six files deep, multiple
false fixes) and is **not documented anywhere obvious**, so it's recorded
here in full detail for anyone touching `backend/app/agent/memory.py`.

CDS's `cuid` mixin (used by both `ChatSessions` and `ChatMessages`)
generates an `ID : UUID` key, which CDS 7.x exposes over OData as
**`Edm.Guid`** — not `Edm.String`. `ChatMessages.session_ID` (the
auto-generated foreign key from the `session : Association to ChatSessions`
field) is also `Edm.Guid`.

CAP's bundled OData V4 server (**OKRA**, the parser shipped with
`@sap/cds`) does **not** follow the OData V4 spec's own typed-literal
syntax for GUIDs in this version. Here's exactly what was tried, in order,
and what happened:

| `$filter` value tried | Result |
|---|---|
| `session_ID eq {value}` (unquoted, no thought given to type) | Initially looked broken, but was actually masked entirely by the missing-`@cap-js/sqlite` crash above — never properly isolated until that was fixed. |
| `session_ID eq '{value}'` (standard string literal) | **400**: `The type 'Edm.Guid' is not compatible to 'Edm.String'` |
| `session_ID eq guid'{value}'` (OData V4 spec's standard GUID literal syntax) | **400**: `Property 'guid' does not exist in type 'SDAgentService.ChatMessages'` — OKRA's parser doesn't recognize the `guid'...'` prefix at all; it tokenizes `guid` as a bare property reference instead. |
| **`session_ID eq {value}` — bare UUID, completely unquoted, no prefix** | ✅ **Works.** This is the only form OKRA accepts for `Edm.Guid` equality comparisons. |

**Rule going forward: never quote a GUID value in `$filter` against this
CAP service, and never use the `guid'...'` prefix. Use the bare value.**
`label` on `ChatSessions`, by contrast, is a plain `String(100)` —
`Edm.String` — and correctly uses standard single-quote string-literal
syntax (`label eq '{value}'`, with `''`-doubling for embedded quotes). The
two columns' filter syntax is not interchangeable.

---

## What changed vs. the original Joule output

| Area | Original Joule code | This implementation |
|---|---|---|
| OData access | FastAPI called SAP sandbox directly | FastAPI → CAP → sandbox/mock (as agreed) |
| Tools | 3 of 5 implemented | All 5 implemented |
| `response.choices.message` | Bug — `choices` is a list | Fixed to `response.choices[0].message` |
| LLM calls per turn (no tools) | 2 (redundant) | 1 |
| Multi-tool calls in one turn | N/A | Run concurrently via `asyncio.gather`, not sequentially |
| Max tool-loop iterations | Silent fallthrough | 3, forces a wrap-up summary, never drops the turn |
| Persistence | Discussed (HANA Cloud), not built | File-based SQLite via CAP, chosen deliberately over HANA for BTP trial reliability (see above) |
| Sequence numbering | N/A | Assigned atomically by CAP (`appendMessage` action), not the stateless Python layer |
| Input validation | Discussed ("via CAP"), not built | Implemented in CAP's `sandbox-client.js`, allow-list regex on every ID field |
| Deployment manifests | Missing | `manifest.yml` added for both services, sized for trial memory quota (192M + 256M) |
| Frontend | Missing entirely | Single static HTML/JS file, no build step, served by CAP — chosen over React/Vite deliberately (see above) |
| Frontend session persistence | N/A | Server-issued UUID via `/api/session/new`, persisted via `sessionStorage` + sidebar history list |
| CORS | N/A | Scoped to `GET`/`POST` + `Content-Type` only; wildcard origin blocked outright if `ENV=production` |
| Sidebar conversation management | N/A | Rename and delete added (see *Frontend notes* below) |
| Assistant-turn persistence ordering | N/A | `save_message` for the assistant's reply now happens **before** the `done` SSE event is yielded, not after (see *Backend notes* below) |

## Security hardening (added after a dedicated security/performance review)

| # | Issue | Fix |
|---|---|---|
| 1 | **IDOR**: client could supply any `session_id` string and read/write another user's chat history | `session_id` must now be a server-issued UUID from `POST /api/session/new`; `POST /api/chat` rejects anything else with 400 |
| 2 | **OData injection**: `materialId` had no character allow-list | `validateMaterialId` now enforces `^[A-Za-z0-9_-]{1,40}$` |
| 3 | **Open CORS + no auth** | CORS scoped to specific methods/headers; refuses to start with wildcard origin if `ENV=production` (auth itself still needed — see Known gaps) |
| 4 | **Error leakage**: raw exception text reached the browser | All exceptions logged server-side only; client receives a generic message |
| 5 | **Mock-fallback logic didn't match docs** | Split into independent `FORCE_MOCK` / `FALLBACK_ON_FAILURE` flags |
| 6 | **No rate limiting** | `slowapi`, 20 requests/minute per IP on `/api/chat` |
| 7 | **Sequence number race condition** | Computed atomically inside a CAP transaction, not client-side |
| 9 | **Sequential tool execution** | Multiple tool calls in one turn now run concurrently via `asyncio.gather` |
| 10/11 | **No HTTP connection reuse** | Pooled `httpx.AsyncClient`, closed cleanly on shutdown |
| 13 | **`MAX_TOOL_ITERATIONS=5`** worst case | Lowered to 3 |

## Frontend notes (`cap-service/app/sd-agent-ui/index.html`)

Single-file static HTML/CSS/JS, no build step, no framework — see rationale
above.

**Sidebar conversation management:**
- **Naming a new conversation**: clicking "+ New conversation" prompts for
  an optional name. Leaving it blank/cancelling falls back to the default
  "New conversation" label.
- **Rename**: hover a conversation row, click the ✏️ icon, enter a new name.
  Renames the local sidebar label only — does not call any backend route.
- **Delete**: hover a conversation row, click the 🗑️ icon, confirm. This
  **only removes the conversation from the local sidebar list** (stored in
  `localStorage`) — it does **not** delete the underlying `ChatSessions`/
  `ChatMessages` rows server-side, since no delete action is exposed by the
  CAP service. The confirmation dialog says this explicitly. Adding a true
  server-side delete would require a new CAP action plus a corresponding
  backend route — not built yet, deliberately, to avoid wiring a
  destructive server call to a single client-side click without a
  dedicated review.

**Two CSS bugs fixed in the sidebar, easy to reintroduce if this file is
restructured:**
1. `#sidebar` and `#session-list` both need `min-height: 0`. Without it,
   nested flex columns with `overflow-y: auto` silently fail to scroll —
   the box just grows past the viewport instead, with literally no visual
   symptom until there's enough content to actually overflow it.
2. `.session-item` needs an explicit `line-height` (currently `1.4`).
   `<button>` elements inherit an inconsistent browser-default
   line-height that's tighter than the rest of the page, causing visible
   text overlap between sidebar rows once there are enough to require
   scrolling. This bug only becomes visible *after* the scroll fix above
   is in place — it surfaced as what looked like a "new" regression
   partway through fixing the first bug, when it had actually been there
   the whole time, just hidden by the sidebar never having scrolled far
   enough to expose it.

## Backend notes (`backend/app/agent/`)

- **`memory.py`** — all persistence to CAP/SQLite. See the GUID literal
  gotcha above before modifying any `$filter` here.
- **`loop.py`** — the core agentic loop. The assistant's final-turn
  `save_message` call happens **before** the `done` SSE event is yielded,
  not after. `run_agent` is an async generator backing a
  `StreamingResponse`; once `done` is yielded, the client may stop reading
  and the underlying connection can be torn down — code placed after an
  async generator's final yield is not guaranteed to execute if the
  consumer stops pulling from it. Persisting before `done` avoids racing
  that teardown.
- Both of `memory.py`'s exception handlers use `exc_info=True` on their
  `logger.warning(...)` calls, deliberately. A generic one-line warning
  with no traceback was the single biggest time-sink during debugging —
  several real, distinct bugs (a missing native dependency, an invalid
  OData filter, a type mismatch) were all hidden behind the same
  unhelpful `"... (CAP/HANA unreachable?)"` message with zero underlying
  error visible anywhere, across both the FastAPI and CAP logs, until
  `exc_info=True` was added.

## Known gaps / things to decide before production

- **Auth**: still no authentication beyond the session-id UUID format check.
  Fine for a trial demo behind a private URL; add SAP XSUAA (or any
  OAuth2/JWT layer) in front of both services before exposing this beyond
  a demo. Rate limiting is a stopgap, not a substitute.
- **SQLite persistence is per-instance and ephemeral across deploys**: it
  survives app restarts (including CF auto-restarts after a crash) but
  **not** a fresh `cf push` — every redeploy re-stages the app onto a
  brand-new container filesystem, so all conversation history is wiped on
  every redeploy, including for unrelated changes like a CSS tweak. This
  was confirmed directly during development (history that worked
  perfectly before a `cf push` for an unrelated UI change came back empty
  immediately after). Acceptable for a demo; if durable history across
  redeploys becomes a real requirement, look at HANA Cloud (if trial
  entitlement allows) or a bound persistent volume service for the SQLite
  file — neither the CAP service code nor the schema needs to change for
  the migration to HANA itself, only the `db` config.
- **CAP input validation** is regex-based on ID formats only — it does not
  check that a customer/order actually exists before hitting the sandbox
  (that's what `get_customer_details` is for).
- **Real sandbox auth**: SAP Business Accelerator Hub sandbox keys are
  rate-limited and reset periodically.
- **Delete only hides conversations locally**, it doesn't remove the
  server-side transcript — see *Frontend notes* above if true deletion
  becomes necessary.
- **No automated test suite checked in** — the agent loop, validation logic,
  and the static UI were all verified manually (including a real
  Playwright browser test) during development. A `pytest`/Playwright suite
  checked into the repo would be a good next step for a portfolio piece.

## Repository structure (for GitHub)

```
.
├── cap-service/
│   ├── app/sd-agent-ui/index.html   ← the entire frontend, one file
│   ├── db/schema.cds
│   ├── srv/
│   ├── package.json
│   ├── manifest.yml
│   └── .env.example
├── backend/
│   ├── app/
│   ├── requirements.txt
│   ├── manifest.yml
│   └── .env.example
└── README.md
```

Suggested `.gitignore` entries: `node_modules/`, `*.sqlite`, `.env`,
`__pycache__/`, `*.pyc`, `venv/`. Also add any one-off debug log dumps
(e.g. `*_logs*.txt`) if you find yourself redirecting `cf logs` output to
a file during troubleshooting — these have no business being committed.