# web/

The BlastRadius interface: a Vite + React + Tailwind app that builds into
`web/dist`, which the FastAPI service mounts at `/`. One origin in development
and in production, so the API needs no CORS handling.

```bash
just web-install   # once
just web-dev       # :5173, proxying /api to the API on :8000
just web-build     # -> web/dist, then `just dev` serves it
```

`web/dist` is gitignored and rebuilt by the Docker image's Node stage. Until
this directory has a `package.json`, that stage produces an empty `dist` and
the API serves its JSON endpoints alone.
