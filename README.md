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

## Known gaps / things to decide before production

- **Auth**: still no authentication beyond the session-id UUID format check.
  Fine for a trial demo behind a private URL; add SAP XSUAA (or any
  OAuth2/JWT layer) in front of both services before exposing this beyond
  a demo. Rate limiting is a stopgap, not a substitute.
- **SQLite persistence is per-instance and ephemeral across deploys**: it
  survives app restarts but not a fresh `cf push` (the filesystem is
  rebuilt). Acceptable for a demo; revisit if you need durable history
  across redeploys.
- **CAP input validation** is regex-based on ID formats only — it does not
  check that a customer/order actually exists before hitting the sandbox
  (that's what `get_customer_details` is for).
- **Real sandbox auth**: SAP Business Accelerator Hub sandbox keys are
  rate-limited and reset periodically.
- **No automated test suite checked in** — the agent loop, validation logic,
  and now the static UI were all verified manually (including a real
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
`__pycache__/`, `*.pyc`, `venv/`.
