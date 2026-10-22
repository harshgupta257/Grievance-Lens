# Deploying the website

The deployed app is the website plus the complaint assistant. It needs **no database and no API key**: it
reads the validated, committed files in `data/processed/` and `data/reference/`. The Part A pipeline
(PDFs → PostgreSQL → exports) runs on your own machine; you commit its outputs and redeploy.

## What is in place

| Item | Where |
|---|---|
| Production image (Python 3.11 slim, non-root user, health check, one worker) | `Dockerfile`, `.dockerignore` |
| Render blueprint (free plan, Docker, `/health` check) | `render.yaml` |
| CI: lint, format check, tests, image build and smoke test on every push | `.github/workflows/ci.yml` |
| Production settings: API docs hidden, HSTS header, proxy-aware rate limiting | `src/app/main.py`, DESIGN.md D-049 |

### Environment variables

| Variable | Default | Production value | Purpose |
|---|---|---|---|
| `APP_ENV` | `development` | `production` (set in the Dockerfile) | Hides `/docs`, `/redoc` and `/openapi.json`; sends `Strict-Transport-Security` |
| `TRUSTED_PROXY_HOPS` | `0` | `1` on Render (check it, below) | How many reverse proxies sit in front of the app. The client address for rate limiting is the N-th entry from the right of `X-Forwarded-For` |
| `PORT` | `8000` | set by the host | Port the server listens on |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `LLM_*` | unset (offline) | optional | LLM routing; see `.env.example` and [routing.md](routing.md). Set the key as a secret in the host's dashboard, never in a file |

An invalid `APP_ENV` or `TRUSTED_PROXY_HOPS` stops the app at startup with a clear message.

## Steps

1. **Put the code on GitHub.**
   ```powershell
   # create an empty repository on github.com first (no README), then:
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
   The **ci** workflow then runs; check the Actions tab shows green (tests and image build).
2. **Create the service on Render.** On render.com, go to New → Blueprint, pick the repository and apply.
   Render builds the `Dockerfile` and gives a URL like `https://cpgrams-insights.onrender.com`.
3. **Open the URL and check:**
   - Every page loads.
   - `/health` shows `"submits_complaints": false`.
   - `/docs` returns 404.
4. **Check the proxy setting** (below).
5. **Optional: a custom domain** (Render → Settings → Custom Domains). HTTPS is automatic.

### Check the proxy setting

`TRUSTED_PROXY_HOPS` must equal the number of proxies that append to `X-Forwarded-For`:

- **Too low (0):** every visitor shares the proxy's address and one rate limit.
- **Too high:** a visitor can forge the header to get unlimited requests.

The test below sends 62 requests from your machine, each with a different forged address. The limit is 60 a
minute offline (10 with an LLM).

```powershell
$u = "https://<your-app>.onrender.com/route"
$body = '{"text":"My speed post parcel has not been delivered for 12 days."}'
1..62 | ForEach-Object {
  $i = $_
  try { Invoke-RestMethod $u -Method Post -ContentType "application/json" -Body $body `
          -Headers @{"X-Forwarded-For"="203.0.113.$i"} | Out-Null; "$i ok" }
  catch { "$i " + [int]$_.Exception.Response.StatusCode }
} | Select-Object -Last 3
```

- **Expected:** the last requests return **429**, because forging does not help.
- **Then:** open the assistant from a phone on mobile data within that minute; it should still work, because
  that is a different client.
- **If the forged requests are never limited:** lower the value.
- **If the phone is also limited:** raise it by one.

## Limits of this setup (stated, not hidden)

- **One process.** The rate limiter lives in memory (D-046), so the app runs one worker. That is ample for a
  portfolio site (about 180 MB of memory at idle in local testing).
  - Scaling to several instances needs a shared limiter (for example Redis) first.
- **Free-plan sleep.** Render's free plan stops the service when idle, so the first visit after a pause takes a
  while to load.
- **Monitoring is the host's logs.** Logs carry method, path, status and time only, plus the error type for
  unhandled errors, and never complaint text.
  - For alerts, add an uptime monitor on `/health`.
  - An error tracker would need its own account and a check that it never captures request bodies.
- **Data updates are manual.** To add new monthly reports:
  1. Run the pipeline locally (README).
  2. Commit the changed `data/processed/` files.
  3. Push; Render redeploys automatically.
- **The Docker image build was not run on the development machine.** Docker Desktop needs WSL2, which needs
  administrator rights. Instead:
  - The image's file set (`src/` and `data/` only) was served locally in production mode, and every page and
    endpoint checked.
  - The CI workflow builds and smoke-tests the real image on every push.
- **Before wider public use:**
  - A clearer "unofficial" name.
  - A short privacy note: complaint text is processed in memory and not stored. With the LLM on, text goes to
    the model provider after the privacy filter.
