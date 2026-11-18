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

By default the app uses an in-browser mock API (`src/mocks/`), so it works without the backend.
A "Mock data" badge shows in the top bar.

To use the real FastAPI backend on port 8000, create `frontend/.env.local`:

```
VITE_USE_MOCK=false
```

and restart `npm run dev`. Vite proxies `/api` to `http://localhost:8000`.

## Screens

| Route | What it shows |
|---|---|
| `/exceptions` | All exceptions, filter by type and status, severity badge, escalated rows marked |
| `/exceptions/:id` | Evidence, timecard timeline (shifts vs punches, proposed punch dashed), proposed fix before/after, rationale, cited policy clause, validator result, AI / rule-based badge, Approve / Reject with comment, history |
| `/audit` | Audit trail, filter by entity ID |

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

## Open question for the backend

`GET /exceptions/{id}` is assumed to return `{ exception, timecard, proposal }` (see `ExceptionDetail` in `src/types/api.ts`). Confirm the shape with Raman.
