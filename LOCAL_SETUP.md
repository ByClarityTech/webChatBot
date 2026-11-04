# Local Development Guide (Without Docker / Containers)

This guide walks you through setting up and running all services of the Public Docs Chatbot natively on Windows without using Docker.

---

## Architecture Overview

```
[ Frontend: Next.js ]   -->   [ Backend API: FastAPI ]   -->   [ Worker: Docling/LlamaIndex ]
    (Port 3000)                     (Port 8000)                         (Port 8001)
                                      |         \                            |
                                      v          \                           v
                              [ PostgreSQL ]      ----->   [ Qdrant Vector DB ]
                               (Port 5432)                     (Port 6333)
```

| Service | Port | Description |
|---|---|---|
| **PostgreSQL** | `5432` | Relational database (LangGraph checkpointer, bookings, OAuth tokens, settings) |
| **Qdrant** | `6333` | Vector database for document embeddings & similarity search |
| **Worker API** | `8001` | FastAPI + Docling + LlamaIndex document parsing and ingestion service |
| **Backend API** | `8000` | FastAPI chat, RAG retrieval, admin endpoints, and business logic |
| **Frontend UI** | `3000` | Next.js chat widget and admin dashboard |

---

## Prerequisites

- **Python 3.11+** (Python 3.12 or 3.13 recommended; `uv` is supported)
- **Node.js 18+** & **npm**
- **PostgreSQL 16+** (Installed and running on port `5432`)

---

## Step 1: Configure Environment Variables

In containerized setups, services communicate using Docker network hostnames (`postgres`, `qdrant`, `worker`). For local execution, point these services to `localhost` and set local paths for caching and uploads.

### Root `.env`

Update or verify the following keys in your root `.env` file:

```ini
# Worker URL
WORKER_URL=http://localhost:8001

# Qdrant URL
QDRANT_URL=http://localhost:6333

# PostgreSQL connection string
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/public_assistant

# Local directories for uploads and caching
DOCLING_CACHE_DIR=./tmp/docling_cache
UPLOAD_DIR=./data/uploads
```

### Frontend `.env.local`

Initialize `frontend/.env.local`:

```powershell
Copy-Item frontend\.env.example frontend\.env.local
```

Ensure `NEXT_PUBLIC_API_URL` points to `http://localhost:8000`:
```ini
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Step 2: Database Setup (PostgreSQL)

Ensure PostgreSQL is running locally on port `5432` and create the `public_assistant` database if it does not already exist:

```powershell
# Using psql on Windows (adjust path if your PostgreSQL version differs):
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE public_assistant;"
```

> **Note:** The backend automatically initializes all required tables and indexes on first startup via `app.db.init.init_db()`.

---

## Step 3: Run Qdrant Vector Database

Qdrant provides a standalone Windows binary that runs directly without containers.

1. **Download and unpack Qdrant**:
   ```powershell
   New-Item -ItemType Directory -Force -Path ".\qdrant-server" | Out-Null
   Invoke-WebRequest -Uri "https://github.com/qdrant/qdrant/releases/download/v1.12.5/qdrant-x86_64-pc-windows-msvc.zip" -OutFile ".\qdrant-server\qdrant.zip"
   Expand-Archive -Path ".\qdrant-server\qdrant.zip" -DestinationPath ".\qdrant-server" -Force
   ```

2. **Start Qdrant** (Keep running in **Terminal 1**):
   ```powershell
   .\qdrant-server\qdrant.exe
   ```
   Qdrant will listen on `http://localhost:6333` and store data locally in `.\storage`.

*(Alternative: You can use a free tier cluster on [Qdrant Cloud](https://cloud.qdrant.io/) and set `QDRANT_URL` in `.env` to your cloud cluster URL).*

---

## Step 4: Start the Ingestion Worker (Port 8001)

The worker handles document parsing via Docling and vector embedding via LlamaIndex.

Open **Terminal 2** at the repository root:

```powershell
cd worker

# Create virtual environment and install dependencies
uv venv --python 3.12 .venv
.\.venv\Scripts\activate
uv pip install -e .

# Or using standard python/pip:
# python -m venv .venv
# .\.venv\Scripts\Activate.ps1
# pip install -e .

# Start the worker
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Health check:
- Open `http://localhost:8001/health` (should return `{"status":"ok"}`).

---

## Step 5: Start the Backend API (Port 8000)

The backend handles the RAG agent, LangGraph Postgres checkpointing, chat completions, bookings, and admin endpoints.

Open **Terminal 3** at the repository root:

```powershell
cd backend

# Create virtual environment and install dependencies
uv venv --python 3.12 .venv
.\.venv\Scripts\activate
uv pip install -e .

# Or using standard python/pip:
# python -m venv .venv
# .\.venv\Scripts\Activate.ps1
# pip install -e .

# Start the backend API
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verification:
- Health check: `http://localhost:8000/api/v1/health`
- Interactive OpenAPI Docs: `http://localhost:8000/docs`

---

### Create the first Super Admin

With the backend virtualenv active, in the `backend` folder:

```powershell
python ..\scripts\create_super_admin.py --email you@example.com --name "Your Name"
```

Enter the password twice when asked (at least 12 characters). Use this account to sign in at `/admin`, then add a client under **Clients** and its first Admin under **Users**.

---

## Step 6: Start the Frontend UI (Port 3000)

Open **Terminal 4** at the repository root:

```powershell
cd frontend
npm install
npm run dev
```

The Next.js application will be available at:
- Chat for one client: `http://localhost:3000/?client=<slug>` (widget view: `http://localhost:3000/embed?client=<slug>`). Without `?client=` the page shows "This assistant is not available". The header and the three question buttons show the client's name from the Clients page.
- Admin Dashboard: [http://localhost:3000/admin](http://localhost:3000/admin)

---

## Step 7: Ingest Documents & Test

Once all services are running:

### 1. Ingest Sample Documentation
From the repository root (activate backend venv or use system python with `httpx` installed):
```powershell
pip install httpx
python scripts/seed_public_docs.py --email you@example.com --client <slug>
```
Documents belong to one client, so create the client on the **Clients** page first. Seeding signs in as a Super Admin or Admin (create one with `scripts/create_super_admin.py`); a Super Admin must pass `--client`, an Admin seeds their own client. The password is read from `SEED_ADMIN_PASSWORD` or prompted for.

Documents uploaded before per-client documents have no client and are ignored by the chatbot. See "Documents from before per-client documents" in the README to give them to a client.

### 2. Test via CLI
```powershell
python scripts/chat_cli.py --client <slug>
```

### 3. Test via API
```powershell
curl -X POST http://localhost:8000/api/v1/chat `
  -H "Content-Type: application/json" `
  -d '{"message": "What services do you offer?", "client": "<slug>"}'
```
