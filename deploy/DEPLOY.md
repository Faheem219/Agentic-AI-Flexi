# Deploying Veritas

The backend (FastAPI + agents + MCP tool server) runs on your VM in Docker. The frontend runs on Vercel. No CI/CD is needed: test locally, then deploy by hand.

## 1. Backend on the VM

Prerequisites: Docker with the compose plugin, and an open TCP port 8000 (or 80/443 if you use the HTTPS option).

```bash
git clone git@github.com:Faheem219/Agentic-AI-Flexi.git
cd Agentic-AI-Flexi/backend
cp .env.example .env
nano .env        # GEMINI_API_KEY=..., CORS_ORIGINS=https://<your-app>.vercel.app
                 # optional: ACCESS_CODE=<code> to stop strangers spending your Gemini quota
                 # optional: OPENALEX_API_KEY=<free key> for a 10x higher daily search budget
docker compose up -d --build
curl http://localhost:8000/api/health    # {"status":"ok", ... "mcp":{"mode":"mcp-stdio", ...}}
```

Data (runs, traces, library documents, memory) lives in the `veritas-data` Docker volume and survives restarts and rebuilds.

To update after a `git pull`: `docker compose up -d --build`. To see logs: `docker compose logs -f`.

The API must run as a single process (`--workers 1`, already set), because runs that are waiting for plan approval are held in memory.

### Optional: HTTPS with your own domain

Point a DNS A record (for example `api.example.com`) at the VM and open ports 80 and 443:

```bash
cd Agentic-AI-Flexi
DOMAIN=api.example.com docker compose -f backend/docker-compose.yml -f deploy/docker-compose.https.yml up -d --build
```

Caddy obtains a Let's Encrypt certificate automatically and streams Server-Sent Events without buffering.

### Without Docker (systemd)

```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp ../deploy/veritas.service /etc/systemd/system/   # edit User / paths first
sudo systemctl daemon-reload && sudo systemctl enable --now veritas
```

## 2. Frontend on Vercel

1. In `frontend/vercel.json`, replace `YOUR_VM_PUBLIC_IP` with the VM's public IP (or use `https://api.example.com` if you set up HTTPS). Vercel then proxies `/api/*` to the VM, so the browser only talks to the HTTPS Vercel domain and no mixed-content or CORS problems arise.
2. Deploy from the `frontend` folder:
   ```bash
   cd frontend
   npx vercel        # first time: link the project and choose "frontend" as the root
   npx vercel --prod
   ```
   Or import the GitHub repo in the Vercel dashboard and set **Root Directory** to `frontend`. The framework is detected as Vite.
3. Add the Vercel URL to `CORS_ORIGINS` in the VM's `.env` and run `docker compose up -d` again. This only matters if you call the API directly by setting `VITE_API_BASE`.

The UI follows runs live over Server-Sent Events. If a proxy buffers the stream, it switches to polling automatically after 12 seconds, so live updates keep working through the Vercel rewrite.

## 3. Smoke test after deploying

- Open the Vercel URL, ask "How does spaced repetition affect long-term retention?" at Quick depth, and watch the agent team work.
- On the Your library page, upload `backend/sample_docs/agentic_ai_study_notes.md`, then ask a question with "Your library" ticked.
- On the Evaluation page you should see the benchmark that ships in `backend/evals/results/latest.json`.
