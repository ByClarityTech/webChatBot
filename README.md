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
Visitor → Next.js Widget (/embed?client=<slug>) → POST /api/v1/chat {client, message}
                              ↓
                    LangGraph (retrieve → generate)
                              ↓
   Qdrant (public_site_docs, filtered by client_id) + PostgreSQL (thread memory)

Admin upload → POST /api/v1/admin/documents/upload → Worker (Docling → LlamaIndex → Qdrant)
```

Every document belongs to one client. Its chunks carry `client_id` and `source_id`, its row lives in `knowledge_sources`, and the chatbot on a client's widget searches only that client's chunks. Conversation threads are tied to the client they started on (`chat_threads`).

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

Documents belong to a client, so create one first (Super Admin, **Clients** page; see **Admin dashboard** below to create the first user). Seeding signs in as a Super Admin or Admin:

```bash
pip install httpx
python scripts/seed_public_docs.py --email you@example.com --client <slug>
```

A Super Admin must pass `--client`; an Admin always seeds their own client and may leave it out. The password is read from `SEED_ADMIN_PASSWORD` or prompted for. Files the client already has are skipped.

### 4. Chat via API

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What services do you offer?", "client": "<slug>"}'
```

### 5. Chat via CLI

```bash
python scripts/chat_cli.py --client <slug>
```

### 6. Chat UI (optional)

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open `http://localhost:3000/?client=<slug>`, or the widget view at `http://localhost:3000/embed?client=<slug>`. Without a known, active client the page shows "This assistant is not available".

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | Health check |
| `POST` | `/api/v1/auth/login` | Admin sign-in (email + password); sets the JWT cookie |
| `POST` | `/api/v1/auth/logout` | Removes the JWT cookie from this browser |
| `GET` | `/api/v1/auth/me` | Signed-in user, user type and client |
| `POST` | `/api/v1/auth/password` | Change your own password (current password required) |
| `GET` / `POST` | `/api/v1/admin/clients` | List or create clients (Super Admin) |
| `GET` / `PATCH` | `/api/v1/admin/clients/{client_id}` | View, rename, suspend or activate a client (Super Admin) |
| `GET` / `POST` | `/api/v1/admin/users` | List or create users (Super Admin: any client; Admin: Support users in own client) |
| `GET` / `PATCH` | `/api/v1/admin/users/{user_id}` | View, rename, deactivate or reset the password of a user |
| `GET` | `/api/v1/admin/documents` | List a client's documents (Super Admin: `?client_id=` required) |
| `POST` | `/api/v1/admin/documents/upload` | Upload and index a document, up to 10 MB (409 if the client already has the file) |
| `DELETE` | `/api/v1/admin/documents/{document_id}` | Remove a document and its chunks |
| `POST` | `/api/v1/admin/documents/{document_id}/reindex` | Re-index one document from its stored file |
| `GET` / `POST` | `/api/v1/admin/faqs` | List a client's FAQs, or add one as a draft |
| `PATCH` / `DELETE` | `/api/v1/admin/faqs/{faq_id}` | Edit, approve (`status: approved`), move back to draft, or delete an FAQ |
| `POST` | `/api/v1/admin/faqs/approve-all` | Approve every draft FAQ of the client |
| `POST` | `/api/v1/admin/faqs/generate` | Draft up to 20 FAQs from the client's ready documents with the LLM |
| `PUT` | `/api/v1/admin/faqs/defaults` | Set the default questions, in order (`faq_ids`, up to 4) |
| `GET` | `/api/v1/widget/config?client=<slug>` | Public: the client's default questions for the widget (404 if unknown or suspended) |
| `POST` | `/api/v1/chat` | Chat with RAG + memory for one client (`client` slug required); booking intent returns `action: "open_booking_form"` |
| `GET` | `/api/v1/admin/google/oauth/start` | Start connecting a client's Google Calendar (Super Admin: `?client_id=`) |
| `GET` | `/api/v1/admin/google/oauth/status` | A client's calendar connection, including `needs_reconnect` |
| `DELETE` | `/api/v1/admin/google/oauth/disconnect` | Disconnect a client's Google Calendar |
| `GET` | `/api/v1/admin/settings/business-hours` | A client's business hours and timezone (server defaults until saved) |
| `PUT` | `/api/v1/admin/settings/business-hours` | Save a client's business hours and timezone |
| `GET` | `/api/v1/admin/dashboard/summary` | Dashboard summary counts (document count per client; Super Admin: all clients, or `?client_id=`) |
| `GET` | `/api/v1/admin/audit-log` | Recent admin audit log entries (admin) |
| `GET` | `/api/v1/booking/availability?client=<slug>&date=` | A client's open slots for a date, in its timezone |
| `POST` | `/api/v1/booking` | Create a booking for a client (`client` slug, name, phone, email, slot, optional `thread_id`) |
| `GET` | `/api/v1/booking/{booking_id}?client=<slug>` | Booking confirmation lookup |
| `GET` | `/docs` | OpenAPI UI |

### Manage the knowledge base

These routes need a signed-in Super Admin or Admin. An Admin always works on their own client; a Super Admin adds `?client_id=<client uuid>` (listed by `GET /api/v1/admin/clients`). Sign in once and reuse the cookie:

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "..."}'
```

List a client's documents (status `processing`, `ready` or `failed`, chunk count, size):

```bash
curl -b cookies.txt "http://localhost:8000/api/v1/admin/documents?client_id=<client uuid>"
```

Upload, then remove one document:

```bash
curl -b cookies.txt -F "file=@faq.md" \
  "http://localhost:8000/api/v1/admin/documents/upload?client_id=<client uuid>"

curl -b cookies.txt -X DELETE \
  "http://localhost:8000/api/v1/admin/documents/<document id>?client_id=<client uuid>"
```

There is no "delete everything" route any more: documents are removed one at a time, inside one client.

> These endpoints are for operators/admins and require sign-in. Website visitors only use the chat widget.

### Bookings and calendar from before per-client booking

Bookings, the Google Calendar connection and saved business hours from before migration 0007 have no client. Give them to one client before migration 0008 applies (dry run first, then `--apply`):

```bash
cd backend
.venv/Scripts/python ../scripts/assign_bookings_to_client.py --client <slug>
.venv/Scripts/python ../scripts/assign_bookings_to_client.py --client <slug> --apply
```

It changes everything in one transaction and refuses anything ambiguous (more than one old connection, a client that already has one, or a booking that clashes with the client's own slots). Migration 0008 then makes the client required and stops with a message if any row still has none.

### Documents from before per-client documents

Documents indexed before migration 0005 have no client, so the chatbot ignores them and the Documents page does not list them. Give them to one client (dry run first, then `--apply`):

```bash
cd backend
.venv/Scripts/python ../scripts/assign_documents_to_client.py --client <slug>
.venv/Scripts/python ../scripts/assign_documents_to_client.py --client <slug> --apply
```

It creates their `knowledge_sources` rows, tags the chunks and moves the stored files to the per-client folders. Pass `--upload-dir` and `--cache-dir` when the worker's `UPLOAD_DIR` and `DOCLING_CACHE_DIR` are not the local defaults. Uploading the files again on the Documents page works too.

## Google Calendar OAuth (Phase B1)

Each client connects its own Google Calendar, which the availability API reads for busy times and where bookings are created. Use the admin UI at `/admin/calendar` or the API directly. The Google OAuth client (step 1) is set up once for the whole server; the connection itself is per client.

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

**Admin UI (recommended):** Sign in at `/admin`, open **Calendar** (a Super Admin chooses the client first), and click **Connect Google Calendar**.

Only the signed-in user who clicked Connect can finish the connection, and only for the client they started it for: the connect link (OAuth state) is stored with both, used once, and expires after 15 minutes. Finish the Google sign-in in the same browser where you are signed in to the admin.

**API / curl:**

```bash
# Returns JSON with auth_url — open it in a browser
curl -b cookies.txt \
  http://localhost:8000/api/v1/admin/google/oauth/start
# Super Admin: .../oauth/start?client_id=<client uuid>

# Or redirect directly from the browser (requires admin auth)
curl -L -b cookies.txt \
  "http://localhost:8000/api/v1/admin/google/oauth/start?redirect=true"
```

After Google redirects back to the API callback URL, you are sent to `/admin/calendar` in the admin UI (a Super Admin comes back to the same client).

If Google later refuses the connection (access revoked, or the refresh token expired), the connection is marked **Reconnect needed**: online booking turns off for that client, visitors get "Online booking isn't available right now", and the Calendar page shows the reason with a **Reconnect Google Calendar** button.

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
curl "http://localhost:8000/api/v1/booking/availability?client=<slug>&date=2026-07-01"
```

Returns `{ "date", "timezone", "slots": [{ "start", "end" }] }`. Slots follow the client's business hours and timezone from **Settings** in the admin UI, or the env vars (`BUSINESS_HOURS_*`, `BUSINESS_DAYS`, slot duration, buffer, `BOOKING_TIMEZONE`) until the client saves its own. Times are in the client's timezone, and the widget shows that timezone next to them.

If the client has no calendar connected, or it needs reconnecting, availability returns `503` "Online booking isn't available right now". An unknown or suspended client gets `404`.

> OAuth admin routes require a signed-in user (except the public Google callback). See the admin dashboard section below.

### Create a booking (Phase B2)

Pick a `slot_start` from the availability response, then:

```powershell
$body = @{
  client = "<slug>"
  name = "Jane Doe"
  phone = "+15551234567"
  email = "jane@example.com"
  slot_start = "2026-07-01T09:30:00+05:30"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/booking" -Method POST -ContentType "application/json" -Body $body
```

The visitor receives a Google Calendar invite at the email provided, from the client's calendar. Check status with `GET /api/v1/booking/{booking_id}?client=<slug>`.

A booking first holds its slot in the database (status `pending`), then creates the Google event, then becomes `confirmed`. A second request for the same slot is refused with `409` before anything is sent to Google, so only one visitor gets an invite. A hold left behind by a crash stops blocking the slot after 3 minutes. Two clients can book the same time, and a cancelled booking frees its slot.

A `thread_id` sent with a booking must be a chat of the same client; another client's (or an unknown) thread is refused with `400`.

### Booking troubleshooting (Phase B5)

| HTTP | Meaning | Fix |
|------|---------|-----|
| `503` | No healthy calendar for this client | Connect (or reconnect) the calendar on the client's **Calendar** page |
| `404` | Unknown or suspended client | Check the `client` slug |
| `409` | Slot taken or unavailable | Pick another slot from availability |
| `429` | Rate limit (`BOOKING_RATE_LIMIT_PER_HOUR`) | Wait and retry |
| `400` | Past slot, or a `thread_id` of another client | Choose a future slot; send the widget's own thread |

Rate limit defaults to **10 bookings per hour** per IP (or IP + `thread_id`). Adjust via `BOOKING_RATE_LIMIT_PER_HOUR` in `.env`.

Set `BOOKING_ENABLED=false` to turn off in-chat booking and the public availability/booking APIs (for example when you only use an external Calendly link). FAQ chat still works; booking-like messages get a text reply without `open_booking_form`.

### Chat booking intent (Phase B3)

When a visitor asks to schedule a meeting in chat, the agent classifies intent and returns `action: "open_booking_form"` (no PII collected in chat — the widget form handles that in B4):

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want to book a demo call", "client": "<slug>"}'
```

FAQ questions still use the RAG path and return `sources[]` as before. Use `python scripts/chat_cli.py` to see the `Action:` line when booking intent is detected.

### Book a meeting in the widget (Phase B4)

With the frontend running and Google Calendar connected (B1):

1. Open `http://localhost:3000/?client=<slug>` or `http://localhost:3000/embed?client=<slug>`.
2. Ask **"Can I schedule a meeting?"** or **"I want to book a demo call"**.
3. The assistant reply appears and an inline **Book a meeting** form opens above the chat input.
4. Enter name, phone, and email; pick a date and an available time slot.
5. Click **Confirm booking** — a confirmation message appears in the chat and the form closes.

If the client's calendar is not connected (or needs reconnecting), the assistant says online booking isn't available and does not open the form. FAQ questions still work normally.

## Embed on your company website

Use the **frontend embed URL**, not the API URL. Each client has its own: add the client's slug as `?client=`.

| Environment | Embed URL |
|-------------|-----------|
| Local dev | `http://localhost:3000/embed?client=<slug>` |
| Production | `https://<your-chat-host>/embed?client=<slug>` |

The widget answers only from that client's documents. An unknown or suspended client shows "This assistant is not available". Restricting which websites may embed a client's widget (allowed domains) is not built yet.

Example: if you deploy the Next.js app to `https://chat.yourcompany.com` and the client's slug is `smile-dental`, embed:

```html
<iframe
  src="https://chat.yourcompany.com/embed?client=smile-dental"
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
NEXT_PUBLIC_CHAT_GREETING=Hi! How can we help you today?
```

The header shows the client's name (from the Clients page) with "AI Assistant" under it. The three question buttons under the greeting are fixed questions with the client's name filled in (`suggestedQuestionTemplates` in `frontend/lib/chat-config.ts`). The FAQ default questions are not shown in the widget at the moment. The greeting and placeholder come from these environment variables and are the same for every client. The chat UI does not show source filenames to visitors.

## Admin dashboard

Admins sign in with email and password. The API answers with a signed JWT (HS256, `JWT_SECRET`, valid 24 hours by default via `JWT_TTL_HOURS`) in an `httpOnly` cookie (`webchatbot_session`), so page scripts cannot read it. Tokens are not stored: each request verifies the signature and expiry, then loads the user, so deactivating a user or suspending a client takes effect immediately, and a password reset ends every token issued before it (`users.tokens_valid_after`). Logout removes the cookie from that browser; a copied token would stay valid until it expires. `JWT_SECRET` is required (at least 32 random characters); changing it signs everyone out.

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
4. **FAQs** — click **Generate FAQs**, check and approve the drafts, and star up to 4 as default questions.
5. **Calendar** — connect Google Calendar (OAuth).
6. **Settings** — set business hours and timezone.
7. **Bookings** — view appointments; cancel one if present.
8. **Clients** (Super Admin) — add a client.
9. **Users** — add an Admin for that client (Super Admin) or a Support user (Admin); copy the generated password.
10. Sign in as the new user in a private window: you are asked to choose your own password first.
11. **Audit log** — see the changes you just made.

The sidebar shows only the pages your role can open. Click your name at the bottom of the sidebar to open **My account**.

### Dashboard home (`/admin`)

Summary cards load from `GET /api/v1/admin/dashboard/summary` (single request): indexed document count, confirmed bookings today (in the client's timezone), and Google Calendar connection status, all for the user's client. A Super Admin sees totals across clients ("today" in the server timezone; the calendar counts as connected when at least one client's is), or one client with `?client_id=`. A static panel notes that chat prompts are code-only in `backend/app/agents/public/prompts/`.

```bash
curl -b cookies.txt \
  http://localhost:8000/api/v1/admin/dashboard/summary
```

### Knowledge base (`/admin/documents`)

After signing in, open **Documents** in the sidebar to upload PDF, Word, PowerPoint, HTML, Markdown or text files (up to 10 MB each), see each file's size, status and chunk count, reindex a document, or delete it. Documents are per client: an Admin sees their own client's; a Super Admin chooses the client first. The same file can be uploaded once per client. A failed upload stays in the list as **Failed** with the reason, and uploading it again replaces it. Uploads go through the Docling → LlamaIndex worker.

```bash
curl -b cookies.txt http://localhost:8000/api/v1/admin/documents
```

### FAQs (`/admin/faqs`)

FAQs are question and answer pairs the chatbot uses before document text. Same roles as Documents (Super Admin, who chooses the client, and Admin).

- **Generate FAQs** sends the client's ready documents to the configured chat model (`LLM_PROVIDER` / `LLM_MODEL`) and saves up to 20 drafts, each linked to its document. Each run reads at most 15,000 characters per document and 60,000 in total; run it again to cover more. Questions the client already has are skipped (same wording, ignoring case); reworded ones can still appear, so check drafts before approving.
- **Add FAQ** saves your own question and answer as a draft (question up to 200 characters, answer up to 2,000, plain text).
- Only **approved** FAQs reach the chatbot: approving indexes the FAQ as one chunk for that client, editing an approved FAQ re-indexes it, and **Unapprove** or **Delete** removes it from the chatbot. **Approve all drafts** approves the rest in one go.
- **Default questions**: star up to 4 approved FAQs and order them with the arrows. They appear as buttons when a visitor opens the chat (from `GET /api/v1/widget/config`). A client with none shows no buttons.
- When the document an FAQ came from is deleted, the FAQ stays and is marked **Source removed**.

In chat, approved FAQ matches are put first in what the model reads and marked as approved, so the model bases its answer on them.

### Business hours (`/admin/settings`)

Configure each client's weekly schedule, slot duration, buffer minutes, and timezone. Super Admins add `?client_id=<client uuid>`; Admin and Support users always work on their own client. Until a client saves its own, the page shows the server defaults (from `.env`) with a note, and those apply to its availability.

```bash
curl -b cookies.txt \
  "http://localhost:8000/api/v1/admin/settings/business-hours?client_id=<client uuid>"
```

### Google Calendar (`/admin/calendar`)

Connect, reconnect or disconnect a client's Google Calendar. Super Admins add `?client_id=<client uuid>`; Admin and Support users always work on their own client. Shows the connected account email, calendar ID, token expiry, and **Reconnect needed** with the reason when Google has refused the connection.

### Bookings (`/admin/bookings`)

View and cancel a client's appointments created via the chat widget or `POST /api/v1/booking`. Super Admins add `?client_id=<client uuid>`; Admin and Support users always work on their own client. Filter by date range and status; cancel removes the event from the client's Google Calendar and sets status to `cancelled`, which frees the slot.

```bash
curl -b cookies.txt http://localhost:8000/api/v1/admin/bookings

curl -X PATCH -b cookies.txt \
  http://localhost:8000/api/v1/admin/bookings/<booking-id>
```

### Clients (`/admin/clients`, Super Admin)

Add a client with a name and a slug (filled in from the name; lowercase letters, digits and hyphens; it cannot be changed later). Rename, suspend or activate a client. Suspending signs out all of the client's users and blocks their sign-in until it is activated again.

### Users (`/admin/users`, Super Admin and Admin)

Super Admins see every user and can filter by client; they add Admins or Support users to an active client. Admins see the users of their own client and add Support users. When a user is created or their password is reset, the browser generates a 20-character password and shows it once with a **Copy** button; it is not stored anywhere else, so pass it to the user privately. Rename, reset password, deactivate and activate appear only on users you may manage (never on Super Admins or yourself).

A user whose password was set by someone else must choose their own on first sign-in: until then every admin page sends them to **My account**, and the API answers `403 Password change required`.

### My account (`/admin/account`, everyone)

Shows your name, email, role and client, and lets you change your password. Changing it signs you out in every other browser; the current one stays signed in.

### Audit log (`/admin/audit-log`, Super Admin and Admin)

Key admin mutations are recorded in Postgres (`admin_audit_log`) with who did it (`user_id`) and which client it concerns (`client_id`): document upload/delete/reindex, booking cancel, business-hours update, calendar connect and disconnect, client create/rename/suspend/activate, user create/update/activate/deactivate/password reset, and own password changes.

Super Admins see every entry and can filter with `?client_id=`. Admins see only their own client's entries. The page shows the latest 50, 100 or 200 entries (the API maximum; there is no paging yet).

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

The database tests in `tests/integration/` (migrations, JWT login, roles, user management, audit trail, `create_super_admin.py`) are skipped unless you point them at a PostgreSQL server where your user may create databases. They create a throwaway database, then drop it; the database named in the URL is not written to:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest
```

Worker tests (Docling, OpenAI and Qdrant are replaced by fakes or an in-memory Qdrant):

```bash
cd worker
pip install -e ".[dev]"
pytest
```

### Ingestion worker is internal

The worker (port 8001) has no sign-in: it trusts the `client_id` the backend sends. Only the backend may reach it. `docker-compose.yml` publishes 8001 on the host for local debugging; do not publish it in production.

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
