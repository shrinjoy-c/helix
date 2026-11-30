# HELIX Frontend

A React + TypeScript + Tailwind research dashboard over `helix.api` (see `../docs/architecture.md`
for the backend's endpoint table). Five sections: Research Workspace, Agent Observatory,
Experiment Lab, Results Explorer, Research Reports - see the main project README for what each
does.

Everything shown comes from the backend. There is no mock/demo data anywhere in this app -
empty states point you at what to do (generate a dataset, launch an experiment) rather than
fabricating placeholder activity.

## Running it

```bash
# 1. Start the backend (from the repo root)
source .venv/bin/activate
uvicorn helix.api.app:app --reload

# 2. Start the frontend (from this directory)
npm install
npm run dev
```

Vite's dev server proxies `/api/*` to `http://127.0.0.1:8000` (see `vite.config.ts`) - no CORS
configuration needed for local development. Open `http://localhost:5173`.

To point at a backend running somewhere else, set `VITE_API_BASE_URL` (e.g.
`VITE_API_BASE_URL=http://my-host:8000 npm run dev`).

## Running the tests

```bash
npm test
```

24 Vitest + React Testing Library tests - the API client, chart palette, status badge, and
the provider selector's actual switching behavior (defaults, conditional fields). No backend
needed; `fetch` is mocked. CI also runs `npm run build` to catch typecheck regressions.

## Stack

React 19, TypeScript, Vite, Tailwind CSS v4, TanStack Query (polling job status, caching),
React Router (hash-based, so this can be served as static files without any server-side
routing config), Recharts (Results Explorer charts, built against the validated categorical
palette from the project's dataviz conventions - see `src/charts/palette.ts`).

## Structure

```
src/
  api/          # client.ts (fetch wrapper), types.ts (mirrors the backend's Pydantic schemas)
  hooks/        # TanStack Query hooks - all data fetching goes through these
  components/   # shared primitives: Card, StatusBadge, Loading/Error/Empty states
  charts/       # palette.ts - validated categorical colors, used unmodified
  layout/       # Sidebar, TopBar (live backend-connectivity indicator)
  pages/        # the five sections, one file each
  setupTests.ts # jest-dom matchers + cleanup, loaded by vitest.config.ts
```

Test files (`*.test.ts`/`*.test.tsx`) are colocated next to what they test, not in a
separate `tests/` tree - `src/api/client.test.ts` sits beside `src/api/client.ts`, etc.

## Known limitations

- No build/deploy pipeline configured yet (just `npm run build` -> static `dist/`).
- The automated suite covers the API client, palette, status badge, and provider selector -
  not yet the five page components themselves (routing, TanStack Query data flow). Those were
  verified manually end-to-end against a live backend instead (see the main README's
  "Running the dashboard" and `docs/architecture.md`'s frontend section for what was checked);
  bringing that under automated coverage too is a natural next increment.
