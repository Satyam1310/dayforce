# Frontend: manager screens

React 18 + TypeScript + Vite + React Router, plain `fetch`, plain CSS. Owner: Priyanshu.

## Run

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # Vitest + React Testing Library
npm run lint
npm run build
```

## Mock vs real backend

In `npm run dev` the app uses an in-browser mock API (`src/mocks/`), so it works without the backend.
A "Mock data" badge shows in the top bar. A production build (`npm run build`, what Docker/Kubernetes
deploys) uses the **real API** by default, so a deployment never shows fake data by accident.
Set `VITE_USE_MOCK=true` at build time only if you really want a mock-data build (e.g. a static demo).

To use the real FastAPI backend on port 8000, create `frontend/.env.local`:

```
VITE_USE_MOCK=false
```

and restart `npm run dev`. Vite proxies `/api` to `http://localhost:8000`.

## Production build (Docker / Kubernetes)

Vite bakes `VITE_*` values into the JavaScript **at build time**, so pass them as Docker build
args (changing them on a running container does nothing).

| Variable            | Production value | Why                                                                                                                            |
| ------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `VITE_USE_MOCK`     | `false`          | Real backend, no sample data. (Unset also means real API in `npm run build`, but set it explicitly.)                           |
| `VITE_API_BASE_URL` | `/api`           | The default. The browser calls the **same origin** (`/api/cases`, ...); nginx forwards `/api/` to the backend. No CORS needed. |

The backend's routes already start with `/api`, so nginx must forward the path **unchanged**
(`proxy_pass` without a trailing path):

```nginx
location /api/ {
    proxy_pass http://backend:8000;   # k8s: the backend Service name and port
    proxy_set_header Host $host;
    proxy_set_header X-Request-ID $request_id;
}
location / {
    try_files $uri /index.html;       # React Router: unknown paths load the app
}
```

Do not set `VITE_API_BASE_URL` to `http://backend:8000`: that name only resolves inside the
cluster, not in the user's browser.

## Screens

| Route             | What it shows                                                                                                                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `/exceptions`     | All exceptions, filter by type and status, severity badge, escalated rows marked                                                                                                                                   |
| `/exceptions/:id` | Evidence, timecard timeline (shifts vs punches, proposed punch dashed), proposed fix before/after, rationale, cited policy clause, validator result, AI / rule-based badge, Approve / Reject with comment, history |
| `/audit`          | Audit trail, filter by entity ID                                                                                                                                                                                   |

## Code map

```
src/
  types/api.ts        TS copies of backend/app/contracts/models.py
  api/                Api interface, real fetch client (http.ts), picks mock or real (index.ts)
  mocks/              seed data (built from contracts/fixtures) + in-memory mock API
  pages/              ExceptionsList, ExceptionDetail, AuditLog
  components/         Timeline, FixDiff, CitationList, ValidationPanel, DecisionForm, badges, states
  lib/                IST formatting helpers, policy clause text
  hooks/useAsync.ts   loading / error / data for API calls
```

## Safety details in the UI

- The manager is the only one who can apply a fix (Approve). Rejecting requires a comment.
- Escalated cases show "AI couldn't safely propose a fix, manager review needed." and no Approve button.
- Timecard `notes` are untrusted and rendered as plain text with a label saying so.
- All times are shown in Asia/Kolkata, whatever the browser's timezone.

## Backend API

The real client (`src/api/http.ts`) calls the endpoints in `backend/API.md`:
`GET /api/cases`, `GET /api/cases/{id}`, `POST /api/cases/{id}/propose`,
`POST /api/cases/{id}/approve`, `POST /api/cases/{id}/reject`, `GET /api/audit`.
A case id is the exception id. The mock API (`src/mocks/mockApi.ts`) follows the same rules.
