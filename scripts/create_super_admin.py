"""Create the first Super Admin, or reset a Super Admin's password.

Super Admins can only be created here, never through the API. Run from the backend folder
with the backend virtualenv so the app's settings and packages are used:

    cd backend
    .venv/Scripts/python ../scripts/create_super_admin.py --email you@example.com --name "Your Name"

The password is prompted for (twice). For non-interactive setups, set SUPER_ADMIN_PASSWORD.

If a Super Admin is locked out (there is no email reset yet), set a new password with:

    .venv/Scripts/python ../scripts/create_super_admin.py --email you@example.com --reset-password
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import psycopg  # noqa: E402
from email_validator import EmailNotValidError, validate_email  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.roles import USER_TYPE_IDS, UserType  # noqa: E402
from app.core.security import hash_password, password_problem, token_cutoff_now  # noqa: E402
from app.db.init import run_migrations  # noqa: E402

SUPER_ADMIN_ID = USER_TYPE_IDS[UserType.super_admin]


def read_password(email: str) -> str:
    password = os.environ.get("SUPER_ADMIN_PASSWORD")
    if password is None:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords do not match.")
    problem = password_problem(password, email=email)
    if problem:
        sys.exit(problem)
    return password


def audit(conn: psycopg.Connection, action: str, user_id) -> None:
    conn.execute(
        """
        INSERT INTO admin_audit_log (action, resource_type, resource_id, client_ip, user_id)
        VALUES (%s, 'user', %s, 'script', NULL)
        """,
        (action, str(user_id)),
    )


def create(conn: psycopg.Connection, email: str, name: str) -> None:
    existing = conn.execute(
        "SELECT id FROM users WHERE LOWER(email) = LOWER(%s)", (email,)
    ).fetchone()
    if existing:
        sys.exit(f"A user with email {email} already exists. Use --reset-password to change it.")
    password = read_password(email)
    user_id = conn.execute(
        """
        INSERT INTO users (client_id, user_type_id, email, password_hash, full_name)
        VALUES (NULL, %s, %s, %s, %s)
        RETURNING id
        """,
        (SUPER_ADMIN_ID, email, hash_password(password), name),
    ).fetchone()[0]
    audit(conn, "super_admin.create", user_id)
    print(f"Created Super Admin {email} (id {user_id}).")


def reset_password(conn: psycopg.Connection, email: str) -> None:
    row = conn.execute(
        "SELECT id, user_type_id FROM users WHERE LOWER(email) = LOWER(%s)", (email,)
    ).fetchone()
    if row is None:
        sys.exit(f"No user with email {email}.")
    user_id, user_type_id = row
    if user_type_id != SUPER_ADMIN_ID:
        sys.exit("That user is not a Super Admin. Reset other users' passwords in the admin app.")
    password = read_password(email)
    # tokens_valid_after ends every login token issued before now. The person running the
    # script chose this password, so no forced change at next sign-in.
    conn.execute(
        """
        UPDATE users
        SET password_hash = %s, is_active = TRUE, tokens_valid_after = %s,
            must_change_password = FALSE, updated_at = NOW()
        WHERE id = %s
        """,
        (hash_password(password), token_cutoff_now(), user_id),
    )
    audit(conn, "super_admin.password_reset", user_id)
    print(f"Password reset for {email}; the account is active and signed out everywhere.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Super Admin or reset its password")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", help="Full name (required when creating)")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Set a new password for an existing Super Admin instead of creating one",
    )
    args = parser.parse_args()

    try:
        email = validate_email(args.email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        sys.exit(f"Invalid email: {exc}")
    if not args.reset_password and not (args.name and args.name.strip()):
        parser.error("--name is required when creating a Super Admin")

    run_migrations()
    with psycopg.connect(get_settings().database_url) as conn:
        if args.reset_password:
            reset_password(conn, email)
        else:
            create(conn, email, args.name.strip())
        conn.commit()


if __name__ == "__main__":
    main()
