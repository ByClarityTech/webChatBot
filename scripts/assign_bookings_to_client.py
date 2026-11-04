"""Give bookings, the Google Calendar connection and business hours from before per-client
booking (migration 0007) to one client.

Those rows have no client_id. Migration 0008 makes client_id required, so run this first.
It only prints what it would do unless --apply is given, and changes everything in one
transaction (all or nothing). Run from the backend folder with the backend virtualenv:

    cd backend
    .venv/Scripts/python ../scripts/assign_bookings_to_client.py --client <slug>
    .venv/Scripts/python ../scripts/assign_bookings_to_client.py --client <slug> --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

import psycopg  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--client", required=True, help="Slug of the client to give them to")
    parser.add_argument("--apply", action="store_true", help="Make the changes (default: dry run)")
    args = parser.parse_args()

    with psycopg.connect(get_settings().database_url) as db:
        row = db.execute(
            "SELECT id, name, status FROM clients WHERE slug = %s", (args.client,)
        ).fetchone()
        if row is None:
            sys.exit(f"No client with slug {args.client!r}. Create it on the Clients page first.")
        client_id, client_name, client_status = row
        print(f"Client: {client_name} ({args.client}, {client_status})")

        bookings = db.execute(
            "SELECT status, COUNT(*) FROM bookings WHERE client_id IS NULL GROUP BY status"
        ).fetchall()
        tokens = db.execute(
            "SELECT connected_email FROM google_tokens WHERE client_id IS NULL"
        ).fetchall()
        hours = db.execute(
            "SELECT timezone FROM business_hours_settings WHERE client_id IS NULL"
        ).fetchall()
        print(f"  bookings without a client: {dict(bookings) or 'none'}")
        print(f"  Google connections without a client: {[t[0] for t in tokens] or 'none'}")
        print(f"  business hours without a client: {[h[0] for h in hours] or 'none'}")

        # A client has at most one connection and one hours row: refuse anything ambiguous.
        problems = []
        if len(tokens) > 1:
            problems.append("more than one Google connection without a client")
        if len(hours) > 1:
            problems.append("more than one business hours row without a client")
        if tokens and db.execute(
            "SELECT 1 FROM google_tokens WHERE client_id = %s", (client_id,)
        ).fetchone():
            problems.append("this client already has a Google connection")
        if hours and db.execute(
            "SELECT 1 FROM business_hours_settings WHERE client_id = %s", (client_id,)
        ).fetchone():
            problems.append("this client already has business hours")
        clashes = db.execute(
            """
            SELECT old.slot_start FROM bookings old
            JOIN bookings mine ON mine.slot_start = old.slot_start
            WHERE old.client_id IS NULL AND old.status IN ('pending', 'confirmed')
              AND mine.client_id = %s AND mine.status IN ('pending', 'confirmed')
            """,
            (client_id,),
        ).fetchall()
        if clashes:
            problems.append(f"{len(clashes)} booking(s) clash with this client's own slots")
        if problems:
            sys.exit("Not changed: " + "; ".join(problems) + ". Fix these first.")

        if not (bookings or tokens or hours):
            print("Nothing without a client. Nothing to do.")
            return
        if not args.apply:
            print("Dry run: nothing changed. Run again with --apply to make these changes.")
            return

        with db.transaction():
            changed = {
                table: db.execute(
                    f"UPDATE {table} SET client_id = %s WHERE client_id IS NULL", (client_id,)
                ).rowcount
                for table in ("bookings", "google_tokens", "business_hours_settings")
            }
        print(f"Done: {changed}")


if __name__ == "__main__":
    main()
