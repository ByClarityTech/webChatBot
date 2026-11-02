# Public Docs Chatbot

Embeddable RAG website assistant — upload your public documentation, chat with grounded answers, cite sources, and keep multi-turn conversation memory.

## Stack

- **Docling** — document parsing (PDF, DOCX, MD, …)
- **LlamaIndex** — chunking, embedding, Qdrant indexing
- **Qdrant** — vector search
- **LangGraph** — retrieve → generate agent with Postgres memory
- **FastAPI** — REST API
- **Next.js + Tailwind** — chat widget (Phase A5)
- **LangSmith** — optional tracing

## Architecture

```
Visitor → Next.js Widget → POST /api/v1/chat
                              ↓
                    LangGraph (retrieve → generate)
                              ↓
              Qdrant (public_site_docs) + PostgreSQL (thread memory)

Upload → POST /api/v1/documents/ingest → Worker (Docling → LlamaIndex → Qdrant)
```

## Quickstart

### 1. Configure environment

```bash
cp .env.example .env
```

Set at minimum:

```bash
OPENAI_API_KEY=sk-...
```

### 2. Start services

```bash
docker compose up --build
```

Wait until all services are healthy:

```bash
curl http://localhost:8000/api/v1/health
```

### 3. Seed sample documents

Seeding signs in as a Super Admin or Admin (see **Admin dashboard** below to create the first user):

```bash
pip install httpx
python scripts/seed_public_docs.py --email you@example.com
```

The password is read from `SEED_ADMIN_PASSWORD` or prompted for.

### 4. Chat via API

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What services do you offer?"}'
```

### 5. Chat via CLI

```bash
python scripts/chat_cli.py
```

### 6. Chat UI (optional)

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | Health check |
| `POST` | `/api/v1/auth/login` | Admin sign-in (email + password); sets the session cookie |
| `POST` | `/api/v1/auth/logout` | Ends the session |
| `GET` | `/api/v1/auth/me` | Signed-in user, user type and client |
| `POST` | `/api/v1/auth/password` | Change your own password (current password required) |
| `GET` / `POST` | `/api/v1/admin/clients` | List or create clients (Super Admin) |
| `GET` / `PATCH` | `/api/v1/admin/clients/{client_id}` | View, rename, suspend or activate a client (Super Admin) |
| `GET` / `POST` | `/api/v1/admin/users` | List or create users (Super Admin: any client; Admin: Support users in own client) |
| `GET` / `PATCH` | `/api/v1/admin/users/{user_id}` | View, rename, deactivate or reset the password of a user |
| `GET` | `/api/v1/documents` | List indexed documents (legacy; Super Admin or Admin) |
| `POST` | `/api/v1/documents/ingest` | Upload and index a document (legacy; Super Admin or Admin) |
| `DELETE` | `/api/v1/documents?filename=faq.md` | Remove one document from the knowledge base |
| `DELETE` | `/api/v1/documents?all=true` | Clear the entire knowledge base |
| `GET` | `/api/v1/admin/documents` | List indexed documents (admin) |
| `POST` | `/api/v1/admin/documents/upload` | Upload and index a document (admin) |
| `DELETE` | `/api/v1/admin/documents/{source_id}` | Remove document by source hash (admin) |
| `POST` | `/api/v1/admin/documents/{source_id}/reindex` | Re-ingest one document (admin) |
| `POST` | `/api/v1/chat` | Chat with RAG + memory; booking intent returns `action: "open_booking_form"` |
| `GET` | `/api/v1/admin/google/oauth/start` | Start Google Calendar OAuth (admin) |
| `GET` | `/api/v1/admin/google/oauth/status` | Google Calendar connection status |
| `DELETE` | `/api/v1/admin/google/oauth/disconnect` | Disconnect Google Calendar (admin) |
| `GET` | `/api/v1/admin/settings/business-hours` | Read business hours config (admin) |
| `PUT` | `/api/v1/admin/settings/business-hours` | Update business hours config (admin) |
| `GET` | `/api/v1/admin/dashboard/summary` | Dashboard summary counts (admin) |
| `GET` | `/api/v1/admin/audit-log` | Recent admin audit log entries (admin) |
| `GET` | `/api/v1/booking/availability` | Available booking slots for a date |
| `POST` | `/api/v1/booking` | Create a booking (name, phone, email, slot) |
| `GET` | `/api/v1/booking/{booking_id}` | Booking confirmation lookup |
| `GET` | `/docs` | OpenAPI UI |

### Manage the knowledge base

These routes need a signed-in Super Admin or Admin. Sign in once and reuse the cookie:

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/v1/auth/login \n  -H "Content-Type: application/json" \n  -d '{"email": "you@example.com", "password": "..."}'
```

List what is indexed:

```bash
curl -b cookies.txt http://localhost:8000/api/v1/documents
```

Remove one file:

```bash
curl -b cookies.txt -X DELETE "http://localhost:8000/api/v1/documents?filename=faq.md"
```

Clear everything:

```bash
curl -b cookies.txt -X DELETE "http://localhost:8000/api/v1/documents?all=true"
```

> These endpoints are for operators/admins and require sign-in. Website visitors only use the chat widget.

## Google Calendar OAuth (Phase B1)

Connect the company Google Calendar so the availability API can read free/busy data. Use the admin UI at `/admin/calendar` or the API directly.

### 1. Google Cloud Console setup

1. Create a project at [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **Google Calendar API**.
3. Configure **OAuth consent screen** (External or Internal).
4. Create **OAuth 2.0 Client ID** (Web application).
5. Add authorized redirect URIs:
   - Local: `http://localhost:8000/api/v1/admin/google/oauth/callback`
   - Railway: `https://<your-railway-api-host>/api/v1/admin/google/oauth/callback`
6. Copy **Client ID** and **Client secret** into `.env`.
7. On Railway, set `ADMIN_FRONTEND_URL` to your Next.js service URL (e.g. `https://<your-railway-frontend-host>`). The OAuth callback stays on the API host; after consent, users are redirected to `/admin/calendar` on the frontend.

Generate a Fernet encryption key for refresh tokens at rest:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set in `.env`:

```bash
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/admin/google/oauth/callback
GOOGLE_TOKEN_ENCRYPTION_KEY=...
ADMIN_FRONTEND_URL=http://localhost:3000
BOOKING_ENABLED=true
BOOKING_TIMEZONE=Asia/Kolkata
```

### 2. Connect calendar

**Admin UI (recommended):** Sign in at `/admin`, open **Calendar**, and click **Connect Google Calendar**.

**API / curl:**

```bash
# Returns JSON with auth_url — open it in a browser
curl -b cookies.txt \
  http://localhost:8000/api/v1/admin/google/oauth/start

# Or redirect directly from the browser (requires admin auth)
curl -L -b cookies.txt \
  "http://localhost:8000/api/v1/admin/google/oauth/start?redirect=true"
```

After Google redirects back to the API callback URL, you are sent to `/admin/calendar` in the admin UI.

Check connection:

```bash
curl -b cookies.txt \
  http://localhost:8000/api/v1/admin/google/oauth/status
```

Disconnect:

```bash
curl -X DELETE -b cookies.txt \
  http://localhost:8000/api/v1/admin/google/oauth/disconnect
```

### 3. Query availability

```bash
curl "http://localhost:8000/api/v1/booking/availability?date=2026-07-01"
```

Returns `{ "date", "timezone", "slots": [{ "start", "end" }] }`. Slots respect business hours from **Settings** in the admin UI (stored in Postgres), or env vars (`BUSINESS_HOURS_*`, `BUSINESS_DAYS`, slot duration, buffer) when no DB row exists.

If calendar is not connected, availability returns `503` with a clear message.

> OAuth admin routes require a signed-in user (except the public Google callback). See the admin dashboard section below.

### Create a booking (Phase B2)

Pick a `slot_start` from the availability response, then:

```powershell
$body = @{
  name = "Jane Doe"
  phone = "+15551234567"
  email = "jane@example.com"
  slot_start = "2026-07-01T09:30:00+05:30"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/booking" -Method POST -ContentType "application/json" -Body $body
```

The visitor receives a Google Calendar invite at the email provided. Confirm the event in your connected calendar and check status with `GET /api/v1/booking/{booking_id}`.

### Booking troubleshooting (Phase B5)

| HTTP | Meaning | Fix |
|------|---------|-----|
| `503` | Calendar not connected | Run OAuth flow: `GET /api/v1/admin/google/oauth/start` |
| `409` | Slot taken or unavailable | Pick another slot from availability |
| `429` | Rate limit (`BOOKING_RATE_LIMIT_PER_HOUR`) | Wait and retry |
| `400` | Past slot or invalid input | Choose a future slot; check email/phone |

Rate limit defaults to **10 bookings per hour** per IP (or IP + `thread_id`). Adjust via `BOOKING_RATE_LIMIT_PER_HOUR` in `.env`.

Set `BOOKING_ENABLED=false` to turn off in-chat booking and the public availability/booking APIs (for example when you only use an external Calendly link). FAQ chat still works; booking-like messages get a text reply without `open_booking_form`.

### Chat booking intent (Phase B3)

When a visitor asks to schedule a meeting in chat, the agent classifies intent and returns `action: "open_booking_form"` (no PII collected in chat — the widget form handles that in B4):

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want to book a demo call"}'
```

FAQ questions still use the RAG path and return `sources[]` as before. Use `python scripts/chat_cli.py` to see the `Action:` line when booking intent is detected.

### Book a meeting in the widget (Phase B4)

With the frontend running and Google Calendar connected (B1):

1. Open [http://localhost:3000](http://localhost:3000) or [http://localhost:3000/embed](http://localhost:3000/embed).
2. Ask **"Can I schedule a meeting?"** or **"I want to book a demo call"**.
3. The assistant reply appears and an inline **Book a meeting** form opens above the chat input.
4. Enter name, phone, and email; pick a date and an available time slot.
5. Click **Confirm booking** — a confirmation message appears in the chat and the form closes.

If calendar is not connected, the slot picker shows the API error (503). FAQ questions still work normally without opening the booking form.

## Embed on your company website

Use the **frontend embed URL**, not the API URL.

| Environment | Embed URL |
|-------------|-----------|
| Local dev | `http://localhost:3000/embed` |
| Production | `https://<your-chat-host>/embed` |

Example: if you deploy the Next.js app to `https://chat.yourcompany.com`, embed:

```html
<iframe
  src="https://chat.yourcompany.com/embed"
  width="400"
  height="600"
  style="border: none; border-radius: 12px;"
  title="Website assistant"
></iframe>
```

The API (`https://api.yourcompany.com`) runs in the background — visitors never open it directly.

### Widget branding (visitor-facing)

Configure `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_URL=https://api.yourcompany.com
NEXT_PUBLIC_CHAT_TITLE=                    # empty = no header title
NEXT_PUBLIC_CHAT_SUBTITLE=                 # empty = no subtitle
NEXT_PUBLIC_CHAT_GREETING=Hi! How can we help you today?
```

Leave `NEXT_PUBLIC_CHAT_TITLE` and `NEXT_PUBLIC_CHAT_SUBTITLE` empty for a clean widget with no product name. The chat UI does not show source filenames to visitors.

## Admin dashboard

Admins sign in with email and password. The session lives in an `httpOnly` cookie (`webchatbot_session`, 8 hours by default, `SESSION_TTL_HOURS`); only a hash of the token is stored in Postgres (`user_sessions`).

There are three user types:

| User type | Belongs to a client | Can use |
|-----------|---------------------|---------|
| Super Admin | No (platform level) | Everything, all clients |
| Admin | Yes | Users, documents, calendar, business hours, audit log; view and cancel bookings |
| Support | Yes | Calendar, business hours, bookings |

All three see the dashboard summary. Super Admins are created only with a script, never through the API. From the `backend` folder, using the backend virtualenv:

```bash
.venv/Scripts/python ../scripts/create_super_admin.py --email you@example.com --name "Your Name"
```

You are asked for the password twice (at least 12 characters). For automated setups, set `SUPER_ADMIN_PASSWORD` instead. If a Super Admin forgets their password, `--reset-password` sets a new one, reactivates the account and signs it out everywhere:

```bash
.venv/Scripts/python ../scripts/create_super_admin.py --email you@example.com --reset-password
```

Super Admins then create clients and their Admins in the admin app; Admins add their own Support users.

Sign in at `http://localhost:3000/admin`, or with curl:

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/v1/auth/login \n  -H "Content-Type: application/json" \n  -d '{"email": "you@example.com", "password": "..."}'
curl -b cookies.txt http://localhost:8000/api/v1/auth/me
```

After 5 failed attempts for the same email and IP within 15 minutes, login returns `429` (`LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_FAILURE_WINDOW_MINUTES`). State-changing admin requests from origins not in `ALLOWED_ORIGINS` are rejected with `403`. The Google OAuth callback remains public for Google redirects.

### Railway deployment

Set these on the **API** service:

| Variable | Purpose |
|----------|---------|
| `ADMIN_FRONTEND_URL` | Next.js base URL — OAuth redirects to `/admin/calendar` here |
| `GOOGLE_OAUTH_REDIRECT_URI` | API callback URL registered in Google Cloud Console |
| `ALLOWED_ORIGINS` | Must include the frontend URL (CORS and the admin CSRF check) |
| `SESSION_COOKIE_SECURE` | Leave unset; the cookie is `Secure` when `APP_ENV=production` |

Set on the **frontend** service:

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_URL` | Public API base URL the admin UI calls |

### Quick walkthrough (&lt; 10 min)

1. Sign in at `/admin` with your email and password.
2. **Home** — summary cards: document count, bookings today, calendar status; prompt deploy note.
3. **Documents** — upload a PDF, confirm it appears in the list.
4. **Calendar** — connect Google Calendar (OAuth).
5. **Settings** — set business hours and timezone.
6. **Bookings** — view appointments; cancel one if present.

### Dashboard home (`/admin`)

Summary cards load from `GET /api/v1/admin/dashboard/summary` (single request): indexed document count, bookings scheduled today (business timezone), and Google Calendar connection status. A static panel notes that chat prompts are code-only in `backend/app/agents/public/prompts/`.

```bash
curl -b cookies.txt \
  http://localhost:8000/api/v1/admin/dashboard/summary
```

### Knowledge base (`/admin/documents`)

After signing in, open **Documents** in the sidebar to upload PDFs, DOCX, Markdown, and other supported files, view chunk counts, reindex a document, or delete it from Qdrant. Uploads use the same Docling → LlamaIndex worker pipeline as the public ingest API.

```bash
curl -b cookies.txt http://localhost:8000/api/v1/admin/documents
```

### Business hours (`/admin/settings`)

Configure weekly schedule, slot duration, buffer minutes, and timezone. Settings are stored in Postgres and override env defaults for the availability API.

```bash
curl -b cookies.txt \
  http://localhost:8000/api/v1/admin/settings/business-hours
```

### Google Calendar (`/admin/calendar`)

Connect or disconnect Google Calendar from the admin UI. Shows connected account email, calendar ID, and token expiry warning when the access token is near expiration.

### Bookings (`/admin/bookings`)

View and cancel appointments created via the chat widget or `POST /api/v1/booking`. Filter by date range and status; cancel removes the Google Calendar event and sets status to `cancelled`.

```bash
curl -b cookies.txt http://localhost:8000/api/v1/admin/bookings

curl -X PATCH -b cookies.txt \
  http://localhost:8000/api/v1/admin/bookings/<booking-id>
```

### Audit log (optional)

Key admin mutations are recorded in Postgres (`admin_audit_log`) with who did it (`user_id`) and which client it concerns (`client_id`): document upload/delete/reindex, booking cancel, business-hours update, calendar disconnect, client create/rename/suspend/activate, user create/update/activate/deactivate/password reset, and own password changes.

Super Admins see every entry and can filter with `?client_id=`. Admins see only their own client's entries.

```bash
curl -b cookies.txt \
  "http://localhost:8000/api/v1/admin/audit-log?limit=50"
```

## Development

### Run tests

```bash
cd backend
pip install -e ".[dev]"
pytest
```

Tests never read your `.env` files and never reach a real database, Qdrant, worker, LLM or Google: `tests/conftest.py` replaces those settings with unreachable addresses and records audit writes in memory.

The database tests in `tests/integration/` (migrations, login and sessions, roles, user management, audit trail, `create_super_admin.py`) are skipped unless you point them at a PostgreSQL server where your user may create databases. They create a throwaway database, then drop it; the database named in the URL is not written to:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest
```

### Switch LLM provider

```bash
LLM_PROVIDER=groq
GROQ_API_KEY=...
LLM_MODEL=llama-3.3-70b-versatile
```

Embeddings remain on OpenAI by default (`EMBEDDING_PROVIDER=openai`).

## Project phases

| Phase | Status |
|-------|--------|
| A0 — Platform bootstrap | Done |
| A1 — Docling + ingest | Done |
| A2 — LangGraph agent | Done |
| A3 — Chat API | Done |
| A4 — Tests + hardening | Done |
| A5 — Next.js widget | Scaffolded |
| A6 — AWS deployment | Planned |
| B1 — Google OAuth + availability | Done |
| B2 — Booking API | Done |
| B3 — Chat booking intent | Done |
| B4 — Widget booking UI | Done |
| B5 — Booking hardening | Done |
| Admin A0 — Auth shell + layout | Done |
| Admin A1 — Knowledge base manager | Done |
| Admin A2 — Business hours + calendar | Done |
| Admin A3 — Bookings list | Done |
| Admin A4 — Hardening + polish | Done |

## License

MIT
