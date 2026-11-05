# Running the project

How to set up and run the web chatbot on a developer machine, and what you need before you start. For step-by-step Windows commands without Docker, see [LOCAL_SETUP.md](../LOCAL_SETUP.md). For API details, see the [README](../README.md).

## What runs

| Service | Port | What it does | Folder |
|---|---|---|---|
| PostgreSQL 16+ | 5432 | Clients, users, bookings, FAQs, settings, audit log, chat memory (LangGraph checkpoints) | external |
| Qdrant 1.12 | 6333 | Document and FAQ search, filtered by client | external |
| Worker (FastAPI) | 8001 | Parses uploads with Docling and indexes them with LlamaIndex | `worker/` |
| Backend API (FastAPI) | 8000 | Chat agent (LangGraph), booking, admin API, login | `backend/` |
| Frontend (Next.js 15) | 3000 | Chat widget (`/embed`) and admin pages (`/admin`) | `frontend/` |

The browser talks only to the frontend and the backend. The backend calls the worker, Qdrant and Postgres.

## What you need

### Software

- Python 3.11 or newer (3.12 is what we use). `uv` is optional but faster than pip.
- Node.js 18 or newer, with npm.
- PostgreSQL 16 or newer. The database user must be allowed to create the `btree_gist` extension, which migration 0009 needs. A database owner can do this.
- Qdrant, either the Windows binary, Docker, or a Qdrant Cloud cluster.
- Docker Desktop, only if you run the services with `docker compose`.
- Git.

### Accounts and keys

| Item | Needed for | Required? |
|---|---|---|
| OpenAI API key | Embeddings always, and chat answers when `LLM_PROVIDER=openai` | Yes |
| Groq or Google (Gemini) API key | Chat answers when `LLM_PROVIDER` is `groq` or `gemini` | Only for that provider |
| Google Cloud OAuth client (ID and secret) | Clients that use Google Calendar | No. Built-in calendar works without it |
| LangSmith API key | Tracing agent runs | No |

Two secrets you generate yourself:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

That gives `JWT_SECRET` (required, at least 32 characters; changing it signs everyone out).

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

That gives `GOOGLE_TOKEN_ENCRYPTION_KEY` (required only for Google Calendar; it encrypts stored Google tokens, so don't change it after clients connect).

## Configure

### Backend and worker: `.env`

Copy the example at the repository root:

```bash
cp .env.example .env
```

Both the backend and the worker read the root `.env` (the backend also reads `backend/.env` if present). The example uses Docker host names. For running natively, change these:

| Setting | Docker value | Native value |
|---|---|---|
| `DATABASE_URL` | `postgresql://agent:agent@postgres:5432/public_assistant` | `postgresql://<user>:<password>@localhost:5432/public_assistant` |
| `QDRANT_URL` | `http://qdrant:6333` | `http://localhost:6333` |
| `WORKER_URL` | `http://worker:8001` | `http://localhost:8001` |
| `UPLOAD_DIR` | `/data/uploads` | `./data/uploads` |
| `DOCLING_CACHE_DIR` | `/tmp/docling_cache` | `./tmp/docling_cache` |

Settings you must fill in:

- `OPENAI_API_KEY`
- `JWT_SECRET`

Settings worth knowing:

| Setting | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `production` makes the login cookie Secure |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Origins allowed to call the API with cookies. Add your frontend host in other environments |
| `LLM_PROVIDER`, `LLM_MODEL` | `openai`, `gpt-4o-mini` | `openai`, `groq` or `gemini` |
| `BOOKING_ENABLED` | `true` | `false` turns off booking for every client |
| `BOOKING_*`, `BUSINESS_*` | see `.env.example` | Server defaults, used until a client saves its own hours on **Settings** |
| `BOOKING_RATE_LIMIT_PER_HOUR` | `10` | Booking requests per visitor per hour |
| `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_FAILURE_WINDOW_MINUTES` | `5`, `15` | Login lockout per email and IP |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | empty | From Google Cloud Console |
| `GOOGLE_OAUTH_REDIRECT_URI` | `http://localhost:8000/api/v1/admin/google/oauth/callback` | Must match the redirect URI registered in Google Cloud exactly |
| `ADMIN_FRONTEND_URL` | `http://localhost:3000` | Where Google sends the admin back after connecting |
| `LANGSMITH_TRACING` | `true` | Only traces when `LANGSMITH_API_KEY` is set |

### Frontend: `frontend/.env.local`

```bash
cp frontend/.env.example frontend/.env.local
```

`NEXT_PUBLIC_API_URL` must point at the backend (`http://localhost:8000` locally). The other values are the widget's default title, greeting and input placeholder.

### Google Calendar (optional)

In Google Cloud Console, create an OAuth client of type *Web application*, enable the Google Calendar API, and add the redirect URI from `GOOGLE_OAUTH_REDIRECT_URI`. While the OAuth consent screen is in testing, add each Google account that will connect as a test user. The README section "Google Calendar OAuth" has the details.

## Run

### Option 1: Docker for the backend services

`docker-compose.yml` starts Postgres, Qdrant, the worker and the backend. The frontend is not in it.

```bash
docker compose up --build
```

```bash
cd frontend
npm install
npm run dev
```

### Option 2: everything native

Start each in its own terminal, in this order. [LOCAL_SETUP.md](../LOCAL_SETUP.md) has the Windows commands for each step.

1. PostgreSQL, with a `public_assistant` database created.
2. Qdrant on port 6333.
3. Worker:
   ```bash
   cd worker
   uv venv --python 3.12 .venv
   uv pip install -e .
   .venv/Scripts/uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
   ```
4. Backend:
   ```bash
   cd backend
   uv venv --python 3.12 .venv
   uv pip install -e ".[dev]"
   .venv/Scripts/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
5. Frontend:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

On macOS or Linux, use `.venv/bin/` instead of `.venv/Scripts/`.

### Database migrations

The backend runs the Alembic migrations (`backend/app/db/migrations/versions`) to the latest version every time it starts. There's no separate migrate step. With `--reload`, a new migration file is applied as soon as it lands, so test a new migration on a throwaway database first.

### Check it's up

| URL | Expect |
|---|---|
| `http://localhost:8000/api/v1/health` | Status of Postgres, Qdrant and the worker |
| `http://localhost:8001/health` | `{"status":"ok"}` |
| `http://localhost:8000/docs` | API docs |
| `http://localhost:3000/admin` | Admin sign-in |

## First-time setup

1. **Create the first Super Admin.** With the backend virtualenv active, in `backend/`:
   ```bash
   python ../scripts/create_super_admin.py --email you@example.com --name "Your Name"
   ```
   It asks for the password twice (at least 12 characters).
2. **Sign in** at `http://localhost:3000/admin`.
3. **Add a client** on **Clients**. Its slug (for example `smile-dental`) identifies its widget.
4. **Add users** on **Users**: an Admin for the client, and optionally a Support user. A user whose password you set must change it on first sign-in.
5. **Upload documents** on **Documents**, or seed sample ones:
   ```bash
   python scripts/seed_public_docs.py --email you@example.com --client <slug>
   ```
   The password comes from `SEED_ADMIN_PASSWORD` or a prompt.
6. **FAQs.** On **FAQs**, generate drafts from the documents, edit them, approve them, and star up to 4 default questions.
7. **Booking.** On **Settings**, set business hours, timezone, buffer and minimum notice. On **Calendar**, choose Google Calendar (and connect it), Built-in calendar, or No online booking.
8. **Open the chat** for the client: `http://localhost:3000/?client=<slug>`, or the widget view `http://localhost:3000/embed?client=<slug>`. Without a known, active client the page shows "This assistant is not available".

A Super Admin picks a client at the top of client-specific pages. Admin and Support users only see their own client.

You can also chat from the terminal:

```bash
python scripts/chat_cli.py --client <slug>
```

## Embedding on a website

Use the frontend's embed URL in an iframe:

```html
<iframe src="https://<your-chat-host>/embed?client=<slug>" width="400" height="600"
  style="border: none; border-radius: 12px;" title="Website assistant"></iframe>
```

Allowed domains aren't built yet, so any site can embed a client's widget.

## Tests

Backend unit tests use fakes and need no services:

```bash
cd backend
.venv/Scripts/python -m pytest -q --ignore=tests/integration
```

Integration tests need a real Postgres where the user may `CREATE DATABASE`. Each run creates and drops a throwaway database; the one in the URL is never written to:

```bash
cd backend
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres .venv/Scripts/python -m pytest -q tests/integration
```

Lint the backend:

```bash
cd backend
.venv/Scripts/ruff check .
```

Frontend type check:

```bash
cd frontend
npx tsc --noEmit
```

`npm run lint` doesn't work yet: ESLint v9 needs a flat config the project doesn't have.

If you run `npm run build` while `npm run dev` is running, the build replaces the dev server's `.next` folder and the dev server breaks. Stop the dev server first, or build in a copy of the folder.

## Common problems

| Symptom | Likely cause |
|---|---|
| Sign-in fails with a 503 about the JWT secret | `JWT_SECRET` missing or shorter than 32 characters |
| Migration 0009 fails on `btree_gist` | The database user can't create extensions. Run `CREATE EXTENSION btree_gist;` as a superuser once |
| Chat page says "This assistant is not available" | Missing `?client=`, wrong slug, or the client is suspended |
| Bot says booking isn't available | The client's calendar is set to No online booking, its Google connection needs reconnecting, or `BOOKING_ENABLED=false` |
| Google sign-in shows `redirect_uri_mismatch` | `GOOGLE_OAUTH_REDIRECT_URI` doesn't exactly match the URI in Google Cloud |
| Google says access blocked | The Google account isn't a test user on the OAuth consent screen |
| Uploads stay "Processing" | Worker not running, wrong `WORKER_URL`, or no `OPENAI_API_KEY` for embeddings |
| Admin calls fail with 403 after a host change | The frontend origin isn't in `ALLOWED_ORIGINS` |
| Signed in but sent back to login | Frontend and backend on different hosts (for example `127.0.0.1` vs `localhost`), so the cookie isn't sent. Use the same host name for both |
