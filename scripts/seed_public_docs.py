"""Seed the knowledge base with sample public-site documents.

Ingesting needs a signed-in Super Admin or Admin. Pass --email (or SEED_ADMIN_EMAIL);
the password is read from SEED_ADMIN_PASSWORD or prompted for.
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


def sign_in(client: httpx.Client, api_url: str, email: str, password: str) -> bool:
    response = client.post(
        f"{api_url.rstrip('/')}/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if response.status_code >= 400:
        print(f"Sign-in failed: {response.status_code} {response.text}")
        return False
    return True


def seed(api_url: str, seed_dir: Path, email: str, password: str) -> int:
    files = sorted(
        path
        for path in seed_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".pptx", ".html", ".md", ".txt"}
    )
    if not files:
        print(f"No seed files found in {seed_dir}")
        return 1

    indexed = 0
    with httpx.Client(timeout=300.0) as client:
        if not sign_in(client, api_url, email, password):
            return 1
        for path in files:
            with path.open("rb") as handle:
                response = client.post(
                    f"{api_url.rstrip('/')}/api/v1/documents/ingest",
                    files={"file": (path.name, handle)},
                )
            if response.status_code >= 400:
                print(f"FAILED {path.name}: {response.status_code} {response.text}")
                return 1

            payload = response.json()
            print(f"Indexed {path.name} -> {payload.get('chunks_indexed', 0)} chunks")
            indexed += 1

        client.post(f"{api_url.rstrip('/')}/api/v1/auth/logout")

    print(f"Done. {indexed} file(s) indexed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed public site documents into Qdrant")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--email", default=os.environ.get("SEED_ADMIN_EMAIL"))
    args = parser.parse_args()

    if not args.email:
        parser.error("--email is required (or set SEED_ADMIN_EMAIL)")
    password = os.environ.get("SEED_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    sys.exit(seed(args.api_url, args.seed_dir, args.email, password))


if __name__ == "__main__":
    main()
