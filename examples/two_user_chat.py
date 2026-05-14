"""Two-user multilingual chat test against a running companion service.

Runs through a scripted conversation as TWO different users speaking
different languages. Verifies:

* Per-user history isolation (Alice's words never leak into Bob's reply).
* Memory works (the bot remembers what each user said earlier).
* Different languages work (English + Spanish in this script).
* Category context flows through (each user is in a different segment).

At the end the script prints exactly how to view the same conversations
in the admin dashboard.

Usage (from the service directory):

    .venv/bin/python examples/two_user_chat.py

Environment variables read:
    BOT_URL              default http://localhost:8765
    BOT_SHARED_SECRET    required — same value as in your .env
    BOT_ADMIN_TOKEN      optional — shown in the final pointer to /admin
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

try:
    import httpx
except ImportError:
    sys.exit(
        "httpx is required. Install with:\n"
        "  pip install httpx\n"
        "or use the service's venv: .venv/bin/python examples/two_user_chat.py"
    )


# ─── Configuration ──────────────────────────────────────────────────────


BOT_URL = os.getenv("BOT_URL", "http://localhost:8765").rstrip("/")
SHARED_SECRET = os.getenv("BOT_SHARED_SECRET", "")
ADMIN_TOKEN = os.getenv("BOT_ADMIN_TOKEN", "")

if not SHARED_SECRET:
    sys.exit(
        "BOT_SHARED_SECRET is not set. Either export it:\n"
        "  export BOT_SHARED_SECRET=$(grep ^BOT_SHARED_SECRET= .env | cut -d= -f2)\n"
        "or run the script with it inline:\n"
        "  BOT_SHARED_SECRET=your_secret .venv/bin/python examples/two_user_chat.py"
    )


# ─── The scripted conversation ──────────────────────────────────────────


@dataclass
class Turn:
    user_id: str
    category_id: str
    category_title: str
    language: str
    text: str


CONVERSATION: list[Turn] = [
    # Alice — English, breast cancer.
    Turn("u_alice_demo", "cat_breast",  "breast_cancer", "en",
         "Hi, I just got my diagnosis last week and I'm overwhelmed."),
    Turn("u_alice_demo", "cat_breast",  "breast_cancer", "en",
         "I'm scared to tell my family. I don't know how to start the conversation."),

    # Bob — Spanish, ovarian cancer (different person, different segment).
    Turn("u_bob_demo",   "cat_ovarian", "ovarian_cancer", "es",
         "Hola. Acabo de empezar la quimioterapia y me siento agotada."),
    Turn("u_bob_demo",   "cat_ovarian", "ovarian_cancer", "es",
         "Tengo miedo de la próxima sesión la semana que viene."),

    # Alice again — memory test. She refers to "what I said about my family".
    Turn("u_alice_demo", "cat_breast",  "breast_cancer", "en",
         "Earlier I mentioned my family — can you remind me what I told you?"),

    # Bob again — memory test in Spanish.
    Turn("u_bob_demo",   "cat_ovarian", "ovarian_cancer", "es",
         "¿Recuerdas lo que te conté sobre la quimioterapia?"),

    # Alice — isolation test. The reply must NOT mention chemotherapy
    # (which is Bob's topic, not Alice's).
    Turn("u_alice_demo", "cat_breast",  "breast_cancer", "en",
         "How can I prepare for my first appointment?"),
]


# ─── Helpers ────────────────────────────────────────────────────────────


def colorize(text: str, code: str) -> str:
    """Add ANSI color if stdout is a TTY."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


BOLD  = lambda s: colorize(s, "1")
DIM   = lambda s: colorize(s, "2")
BLUE  = lambda s: colorize(s, "34")
GREEN = lambda s: colorize(s, "32")
RED   = lambda s: colorize(s, "31")
YELLOW = lambda s: colorize(s, "33")


def send_message(turn: Turn) -> dict:
    payload = {
        "user_id": turn.user_id,
        "text": turn.text,
        "category": {
            "id": turn.category_id,
            "title": turn.category_title,
            "language": turn.language,
        },
        "authorization_key": SHARED_SECRET,
        "timestamp": int(time.time()),
    }
    response = httpx.post(f"{BOT_URL}/bot/reply", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def fmt_reply(text: str, max_chars: int = 400) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ─── Main ───────────────────────────────────────────────────────────────


def main() -> int:
    print(BOLD("\nTwo-user multilingual chat test"))
    print(DIM(f"Service: {BOT_URL}"))
    print(DIM(f"Conversation: {len(CONVERSATION)} messages across 2 users\n"))

    for idx, turn in enumerate(CONVERSATION, start=1):
        label = f"[{idx}/{len(CONVERSATION)}] {turn.user_id} ({turn.language}, {turn.category_title})"
        print(BOLD(label))
        print(BLUE(f"  user:  {turn.text}"))
        try:
            data = send_message(turn)
            reply = fmt_reply(data["reply"])
            print(GREEN(f"  bot:   {reply}"))
            print(DIM(f"  via:   {data['model_used']}  prompt={data['prompt_version']}  request_id={data['request_id'][:10]}…"))
        except httpx.HTTPStatusError as exc:
            print(RED(f"  ERROR: HTTP {exc.response.status_code}"))
            print(RED(f"  {exc.response.text}"))
        except httpx.RequestError as exc:
            print(RED(f"  ERROR: cannot reach {BOT_URL} — is the service running?"))
            print(RED(f"  {exc}"))
            return 1
        print()
        time.sleep(0.3)   # small gap so logs are readable in real time

    # ── Tell the user how to inspect the result ────────────────────────
    print(BOLD("─" * 64))
    print(BOLD("\nWhat to check next\n"))

    print(YELLOW("1. Did the bot remember each user's own context?"))
    print("   In turn 5 (Alice asks about her family) the reply should reference")
    print("   what she said in turn 2. The reply should NOT mention chemotherapy.")
    print("   In turn 6 (Bob asks about chemo) the reply should reference")
    print("   what he said in turn 3-4 — and should NOT mention Alice's family.\n")

    print(YELLOW("2. View it in the admin dashboard:"))
    print(f"   Open {BOT_URL}/admin in a browser.")
    if ADMIN_TOKEN:
        print(f"   Sign in with: {ADMIN_TOKEN}")
    else:
        print("   Sign in with the BOT_ADMIN_TOKEN from your .env.")
    print("   You should see u_alice_demo and u_bob_demo in the user list.")
    print("   Click each user to see their full chat.\n")

    print(YELLOW("3. Or pull data directly via curl:"))
    token = ADMIN_TOKEN or "$BOT_ADMIN_TOKEN"
    print(f'   curl -H "Authorization: Bearer {token}" {BOT_URL}/admin/stats')
    print(f'   curl -H "Authorization: Bearer {token}" {BOT_URL}/admin/users')
    print(f'   curl -H "Authorization: Bearer {token}" {BOT_URL}/admin/users/u_alice_demo')
    print(f'   curl -H "Authorization: Bearer {token}" {BOT_URL}/admin/users/u_bob_demo\n')

    print(YELLOW("4. Want a clean slate next time?"))
    print(f'   curl -X DELETE -H "Authorization: {SHARED_SECRET}" \\\n'
          f'        {BOT_URL}/bot/history/u_alice_demo')
    print(f'   curl -X DELETE -H "Authorization: {SHARED_SECRET}" \\\n'
          f'        {BOT_URL}/bot/history/u_bob_demo')
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
