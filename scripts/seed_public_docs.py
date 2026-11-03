"""Seed a client's knowledge base with sample public-site documents.

Uploading needs a signed-in Super Admin or Admin. Pass --email (or SEED_ADMIN_EMAIL);
the password is read from SEED_ADMIN_PASSWORD or prompted for. Documents belong to one
client: a Super Admin must pass --client <slug>; an Admin always seeds their own client.
Files the client already has are skipped, so the script can be run again.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import httpx

DEFAULT_API_URL = "http://localhost:8000"
SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "seed" / "public"
SEED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".txt"}


def sign_in(client: httpx.Client, api_url: str, email: str, password: str) -> bool:
    response = client.post(
        f"{api_url.rstrip('/')}/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if response.status_code >= 400:
        print(f"Sign-in failed: {response.status_code} {response.text}")
        return False
    return True


def target_client_id(client: httpx.Client, api_url: str, slug: str | None) -> str | None:
    """client_id to send: the chosen client for a Super Admin, None for an Admin (own client).

    Exits when the slug is missing or does not match."""
    me = client.get(f"{api_url.rstrip('/')}/api/v1/auth/me").json()
    if me["user_type"] != "super_admin":
        own = me["client"]["slug"]
        if slug and slug != own:
            sys.exit(f"{me['email']} belongs to client {own!r}, not {slug!r}.")
        print(f"Seeding client {own!r}")
        return None

    if not slug:
        sys.exit("A Super Admin must choose the client: pass --client <slug>.")
    clients = client.get(f"{api_url.rstrip('/')}/api/v1/admin/clients").json()["clients"]
    match = next((c for c in clients if c["slug"] == slug), None)
    if match is None:
        sys.exit(f"No client with slug {slug!r}. Create it on the Clients page first.")
    print(f"Seeding client {slug!r}")
    return match["id"]


def seed(api_url: str, seed_dir: Path, email: str, password: str, slug: str | None) -> int:
    files = sorted(
        path
        for path in seed_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SEED_EXTENSIONS
    )
    if not files:
        print(f"No seed files found in {seed_dir}")
        return 1

    indexed = skipped = 0
    with httpx.Client(timeout=300.0) as client:
        if not sign_in(client, api_url, email, password):
            return 1
        client_id = target_client_id(client, api_url, slug)
        params = {"client_id": client_id} if client_id else {}
        for path in files:
            with path.open("rb") as handle:
                response = client.post(
                    f"{api_url.rstrip('/')}/api/v1/admin/documents/upload",
                    params=params,
                    files={"file": (path.name, handle)},
                )
            if response.status_code == 409:
                print(f"Skipped {path.name}: {response.json().get('detail')}")
                skipped += 1
                continue
            if response.status_code >= 400:
                print(f"FAILED {path.name}: {response.status_code} {response.text}")
                return 1

            payload = response.json()
            print(f"Indexed {path.name} -> {payload.get('chunk_count', 0)} chunks")
            indexed += 1

        client.post(f"{api_url.rstrip('/')}/api/v1/auth/logout")

    print(f"Done. {indexed} file(s) indexed, {skipped} already there.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed sample documents into a client's knowledge base")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--email", default=os.environ.get("SEED_ADMIN_EMAIL"))
    parser.add_argument("--client", help="Client slug (required for a Super Admin)")
    args = parser.parse_args()

    if not args.email:
        parser.error("--email is required (or set SEED_ADMIN_EMAIL)")
    password = os.environ.get("SEED_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    sys.exit(seed(args.api_url, args.seed_dir, args.email, password, args.client))


if __name__ == "__main__":
    main()
