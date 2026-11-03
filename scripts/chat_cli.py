"""Interactive terminal chat against the running API, as a visitor of one client's widget."""

from __future__ import annotations

import argparse
import sys

import httpx

DEFAULT_API_URL = "http://localhost:8000"


def chat_loop(api_url: str, client_slug: str) -> int:
    thread_id: str | None = None
    print(f"Public Docs Chatbot CLI for {client_slug!r} (type 'exit' to quit)\n")

    with httpx.Client(timeout=120.0) as client:
        while True:
            try:
                message = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                return 0

            if not message:
                continue
            if message.lower() in {"exit", "quit"}:
                print("Bye.")
                return 0

            payload: dict[str, str] = {"message": message, "client": client_slug}
            if thread_id:
                payload["thread_id"] = thread_id

            response = client.post(f"{api_url.rstrip('/')}/api/v1/chat", json=payload)
            if response.status_code >= 400:
                print(f"Error: {response.status_code} {response.text}\n")
                continue

            data = response.json()
            thread_id = data["thread_id"]
            print(f"\nAssistant: {data['reply']}")
            if data.get("action"):
                print(f"Action: {data['action']}")
            if data.get("sources"):
                print("Sources:")
                for source in data["sources"]:
                    print(f"  - {source['filename']}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the public docs assistant")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--client", required=True, help="Slug of the client whose assistant to use")
    args = parser.parse_args()
    sys.exit(chat_loop(args.api_url, args.client))


if __name__ == "__main__":
    main()
