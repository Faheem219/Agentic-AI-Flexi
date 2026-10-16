# Deploying Veritas

- **Backend:** runs on your VM in a tmux session on `127.0.0.1:8000`. Your existing Nginx site (with its SSL certificate) forwards your domain to it.
- **Frontend:** runs on Vercel and calls the backend domain directly.

No CI/CD is involved: test locally, then deploy by hand.

```
Browser ──HTTPS──> Vercel (React app)
   │
   └──HTTPS──> https://api.yourdomain.com ──Nginx (SSL)──> 127.0.0.1:8000 (uvicorn in tmux)
```

## 1. Backend on the VM

Prerequisites: git, and Python 3.10+ (3.12 recommended). If `uv` is installed, `run.sh` uses it and fetches Python 3.12 automatically.

```bash
git clone https://github.com/Faheem219/Agentic-AI-Flexi.git     # first time; later: git pull
cd Agentic-AI-Flexi/backend
cp .env.example .env
nano .env
```

Set these in `backend/.env`:

```ini
GEMINI_API_KEY=<your Gemini key>
CORS_ORIGINS=https://<your-project>.vercel.app      # exact Vercel URL, no trailing slash
# optional:
CORS_ORIGIN_REGEX=https://<your-project>-.*\.vercel\.app   # also allow Vercel preview deployments
ACCESS_CODE=<code>          # stops strangers from spending your Gemini quota (enter it once in the UI)
OPENALEX_API_KEY=<key>      # free key from openalex.org/settings/api: about 1,000 searches/day instead of about 100
```

Start it in tmux:

```bash
tmux new -s veritas
./run.sh                      # from Agentic-AI-Flexi/backend
# detach with Ctrl+B then D; re-attach later with: tmux attach -t veritas
```

`run.sh` creates `backend/.venv` on first run, installs or updates dependencies on every run, and starts uvicorn on `127.0.0.1:8000` with one worker. One worker is deliberate: runs waiting for plan approval are held in memory. Runs, traces, library documents and memory are stored in `backend/data/veritas.db`.

Check it: `curl https://api.yourdomain.com/api/health` should return `{"status":"ok", ... "mcp":{"mode":"mcp-stdio", ...}}`.

**To update later:** `git pull`, then in the tmux session press Ctrl+C and run `./run.sh` again.

### Nginx notes

Your existing `location / { proxy_pass http://127.0.0.1:8000; ... }` block works as it is. Two optional additions:

```nginx
client_max_body_size 20m;    # allow PDF uploads to "Your library" (the Nginx default is only 1 MB)
proxy_read_timeout 300s;     # keep long live streams open (the app also pings every 15 s)
```

The API already sends `X-Accel-Buffering: no` on its live event stream, so Nginx does not buffer it, and no extra SSE configuration is needed. Reload Nginx after any change with `sudo nginx -t && sudo systemctl reload nginx`.

## 2. Frontend on Vercel

1. Import the GitHub repository in Vercel and set **Root Directory** to `frontend`. Vite is detected automatically; `frontend/vercel.json` sets the build and routing.
2. In **Project Settings → Environment Variables**, add:
   ```
   VITE_API_BASE = https://api.yourdomain.com
   ```
   Use only the domain: **no `/api` and no trailing slash**. The app adds `/api/...` itself, and a stray `/api` is ignored anyway. The value is built into the bundle, so **redeploy after changing it**.
3. Deploy, then put the exact Vercel URL into `CORS_ORIGINS` on the VM (step 1) and restart `./run.sh`.

## 3. Smoke test

- Open the Vercel URL. The sidebar footer should say "Connected, tools over MCP (stdio)".
- Ask "How does spaced repetition affect long-term retention?" at **Quick** depth and watch the agents work.
- On **Your library**, upload `backend/sample_docs/agentic_ai_study_notes.md`. Then ask a question with "Your library" ticked.
- **Evaluation** shows the benchmark from `backend/evals/results/latest.json`.

If the page loads but shows "Server unreachable", check three things. Is `VITE_API_BASE` exactly your backend domain (then redeploy)? Is the Vercel URL in `CORS_ORIGINS` (then restart `run.sh`)? Does `https://api.yourdomain.com/api/health` open in the browser?

## Alternatives (not needed for the setup above)

`backend/Dockerfile` and `backend/docker-compose.yml` run the same API in Docker. `deploy/docker-compose.https.yml` with the `Caddyfile` adds automatic HTTPS. `deploy/veritas.service` runs it under systemd instead of tmux.
