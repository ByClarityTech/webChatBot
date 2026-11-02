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

```bash
pip install httpx
python scripts/seed_public_docs.py
```

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
| `GET` | `/api/v1/documents` | List indexed documents (legacy public) |
| `POST` | `/api/v1/documents/ingest` | Upload and index a document (legacy public) |
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

List what is indexed:

```bash
curl http://localhost:8000/api/v1/documents
```

Remove one file:

```bash
curl -X DELETE "http://localhost:8000/api/v1/documents?filename=faq.md"
```

Clear everything:

```bash
curl -X DELETE "http://localhost:8000/api/v1/documents?all=true"
```

> These endpoints are for operators/admins. Protect them in production (VPN, API key, or internal network only). Website visitors only use the chat widget.

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
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://localhost:8000/api/v1/admin/google/oauth/start

# Or redirect directly from the browser (requires admin auth)
curl -L -H "Authorization: Bearer $ADMIN_API_KEY" \
  "http://localhost:8000/api/v1/admin/google/oauth/start?redirect=true"
```

After Google redirects back to the API callback URL, you are sent to `/admin/calendar` in the admin UI.

Check connection:

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://localhost:8000/api/v1/admin/google/oauth/status
```

Disconnect:

```bash
curl -X DELETE -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://localhost:8000/api/v1/admin/google/oauth/disconnect
```

### 3. Query availability

```bash
curl "http://localhost:8000/api/v1/booking/availability?date=2026-07-01"
```

Returns `{ "date", "timezone", "slots": [{ "start", "end" }] }`. Slots respect business hours from **Settings** in the admin UI (stored in Postgres), or env vars (`BUSINESS_HOURS_*`, `BUSINESS_DAYS`, slot duration, buffer) when no DB row exists.

If calendar is not connected, availability returns `503` with a clear message.

> OAuth admin routes require `ADMIN_API_KEY` (except the public Google callback). See admin dashboard section below.

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

Generate a strong `ADMIN_API_KEY` and set it in `.env` (API) — use the same value when signing in at `/admin`. Example generators:

```bash
openssl rand -hex 32
# or
python -c "import secrets; print(secrets.token_hex(32))"
```

Open `http://localhost:3000/admin` — you will be prompted for the key, which is stored in `sessionStorage` for the browser session. Protected admin API routes require `Authorization: Bearer <ADMIN_API_KEY>`; the OAuth callback remains public for Google redirects. Login attempts are rate-limited per client IP (`ADMIN_LOGIN_RATE_LIMIT_PER_HOUR`, default `20`).

### Railway deployment

Set these on the **API** service:

| Variable | Purpose |
|----------|---------|
| `ADMIN_API_KEY` | Bearer token for all `/api/v1/admin/*` routes (except OAuth callback) |
| `ADMIN_FRONTEND_URL` | Next.js base URL — OAuth redirects to `/admin/calendar` here |
| `GOOGLE_OAUTH_REDIRECT_URI` | API callback URL registered in Google Cloud Console |
| `ADMIN_LOGIN_RATE_LIMIT_PER_HOUR` | Optional brute-force protection (default `20`) |

Set on the **frontend** service:

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_URL` | Public API base URL the admin UI calls |

### Quick walkthrough (&lt; 10 min)

1. Sign in at `/admin` with your `ADMIN_API_KEY`.
2. **Home** — summary cards: document count, bookings today, calendar status; prompt deploy note.
3. **Documents** — upload a PDF, confirm it appears in the list.
4. **Calendar** — connect Google Calendar (OAuth).
5. **Settings** — set business hours and timezone.
6. **Bookings** — view appointments; cancel one if present.

### Dashboard home (`/admin`)

Summary cards load from `GET /api/v1/admin/dashboard/summary` (single request): indexed document count, bookings scheduled today (business timezone), and Google Calendar connection status. A static panel notes that chat prompts are code-only in `backend/app/agents/public/prompts/`.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://localhost:8000/api/v1/admin/dashboard/summary
```

### Knowledge base (`/admin/documents`)

After signing in, open **Documents** in the sidebar to upload PDFs, DOCX, Markdown, and other supported files, view chunk counts, reindex a document, or delete it from Qdrant. Uploads use the same Docling → LlamaIndex worker pipeline as the public ingest API.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" http://localhost:8000/api/v1/admin/documents
```

### Business hours (`/admin/settings`)

Configure weekly schedule, slot duration, buffer minutes, and timezone. Settings are stored in Postgres and override env defaults for the availability API.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://localhost:8000/api/v1/admin/settings/business-hours
```

### Google Calendar (`/admin/calendar`)

Connect or disconnect Google Calendar from the admin UI. Shows connected account email, calendar ID, and token expiry warning when the access token is near expiration.

### Bookings (`/admin/bookings`)

View and cancel appointments created via the chat widget or `POST /api/v1/booking`. Filter by date range and status; cancel removes the Google Calendar event and sets status to `cancelled`.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" http://localhost:8000/api/v1/admin/bookings

curl -X PATCH -H "Authorization: Bearer $ADMIN_API_KEY" \
  http://localhost:8000/api/v1/admin/bookings/<booking-id>
```

### Audit log (optional)

Key admin mutations are recorded in Postgres (`admin_audit_log`): document upload/delete/reindex, booking cancel, business-hours update, calendar disconnect.

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  "http://localhost:8000/api/v1/admin/audit-log?limit=50"
```

## Development

### Run tests

```bash
cd backend
pip install -e ".[dev]"
pytest
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
