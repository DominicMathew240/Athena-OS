# Athena OS landing page

Static React (Vite) site for the public Athena OS page. **No backend, no sign-in, no API.**

This branch is only the marketing page. The product source lives on `main`.

## Local

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
npm run preview
```

Output is `dist/`. GitHub Pages deploys that folder from this branch.

## What changed from the Django page

- Visual design, copy, and the canned hero demo are the same.
- Sign-in and “Start an inspection” links are gone (there is no app on this host).
- Primary calls to action go to the GitHub repository.

## Licence

Apache-2.0 for the code. Fonts are SIL OFL 1.1 (`src/assets/fonts/OFL-*.txt`). Model weights are not shipped.


## Deploy

The Vite app lives in `frontend/` (a `package.json` is also at the repo root so hosts that look either place can build).

- Build command: `npm run build`
- Publish directory: `dist`
- Node: 22
- Root / base directory: leave empty (or set it to `frontend` and publish `dist`)
