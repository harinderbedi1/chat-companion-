"""Multi-user chat test — 4 users, 4 languages, with memory checks.

Each user has a distinct life situation (returning to work, child asking
questions, loss of appetite, waiting for scan results). The last 4 turns
ask each user's bot to recall what THEY said earlier — the reply must
reference their own context and never another user's.

Run from the service directory:

    export $(grep -v '^#' .env | xargs)
    .venv/bin/python examples/multi_user_chat.py

Environment vars read:
    BOT_URL              default http://localhost:8765
    BOT_SHARED_SECRET    required — same as in .env
    BOT_ADMIN_TOKEN      optional — printed at the end as a sign-in hint
"""

import os
import sys
import time
from dataclasses import dataclass

# httpx is already a service dependency. Its sync API is a drop-in for
# `requests`; the calls below read the same.
try:
    import httpx
except ImportError:
    sys.exit("httpx not installed. Run from the service venv:\n"
             "    .venv/bin/python examples/multi_user_chat.py")


# ── Configuration ──────────────────────────────────────────────────────


BOT_URL = os.getenv("BOT_URL", "http://localhost:8765").rstrip("/")
SECRET = os.getenv("BOT_SHARED_SECRET", "")
ADMIN_TOKEN = os.getenv("BOT_ADMIN_TOKEN", "")

if not SECRET:
    sys.exit(
        "BOT_SHARED_SECRET is not set.\n"
        "Try:  export $(grep -v '^#' .env | xargs)"
    )


# ── The conversation script ────────────────────────────────────────────


@dataclass
class Turn:
    user_id: str
    lang: str
    category: str
    text: str
    # A short tag describing the topic. Used at the end for memory-check
    # assertions in your head: each user's reply should mention their own
    # tag and none of the others'.
    topic: str = ""


CONVERSATION: list[Turn] = [
    # ── Round 1 — each user opens a different conversation ──────────
    Turn("u_alice",   "en", "breast_cancer",
         "First time posting. I'm thinking about going back to work soon.",
         topic="work"),
    Turn("u_sofia",   "es", "ovarian_cancer",
         "Mi hija de seis años pregunta por qué pierdo el cabello.",
         topic="daughter"),
    Turn("u_camille", "fr", "breast_cancer",
         "Je n'ai presque plus d'appétit depuis ma dernière séance.",
         topic="appetite"),
    Turn("u_ingrid",  "de", "lung_cancer",
         "Ich warte heute auf die Ergebnisse meines Scans.",
         topic="scan results"),

    # ── Round 2 — each user adds a follow-up detail ─────────────────
    Turn("u_alice",   "en", "breast_cancer",
         "My manager doesn't know yet. The meeting is on Friday.",
         topic="work"),
    Turn("u_sofia",   "es", "ovarian_cancer",
         "No sé cómo explicárselo a una niña tan pequeña.",
         topic="daughter"),
    Turn("u_camille", "fr", "breast_cancer",
         "Mon mari essaie de cuisiner mes plats préférés mais rien ne passe.",
         topic="appetite"),
    Turn("u_ingrid",  "de", "lung_cancer",
         "Der Anruf vom Arzt soll heute Nachmittag kommen.",
         topic="scan results"),

    # ── Round 3 — memory checks. The reply must reference THIS user's
    #     own topic and never mention any other user's topic. ──────────
    Turn("u_alice",   "en", "breast_cancer",
         "When did I say my meeting was?",
         topic="work"),
    Turn("u_sofia",   "es", "ovarian_cancer",
         "¿Recuerdas quién me hizo esa pregunta?",
         topic="daughter"),
    Turn("u_camille", "fr", "breast_cancer",
         "Que t'ai-je dit sur mon mari?",
         topic="appetite"),
    Turn("u_ingrid",  "de", "lung_cancer",
         "Was habe ich gerade über den Anruf gesagt?",
         topic="scan results"),
]


# ── HTTP helpers ───────────────────────────────────────────────────────


def send(turn: Turn) -> dict:
    payload = {
        "user_id": turn.user_id,
        "text": turn.text,
        "category": {
            "id": f"cat_{turn.category}",
            "title": turn.category,
            "language": turn.lang,
        },
        "authorization_key": SECRET,
        "timestamp": int(time.time()),
    }
    response = httpx.post(f"{BOT_URL}/bot/reply", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def short(text: str, n: int = 200) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


# ── Pretty terminal output ─────────────────────────────────────────────


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


BOLD   = lambda s: c("1",  s)
DIM    = lambda s: c("2",  s)
RED    = lambda s: c("31", s)
GREEN  = lambda s: c("32", s)
YELLOW = lambda s: c("33", s)


# ── Main ───────────────────────────────────────────────────────────────


def main() -> int:
    print(BOLD(f"\nMulti-user chat test — {BOT_URL}"))
    print(DIM(f"{len(CONVERSATION)} turns across 4 users in 4 languages\n"))

    for i, turn in enumerate(CONVERSATION, start=1):
        tag = f"[{i:2}/{len(CONVERSATION)}] {turn.user_id} ({turn.lang}, {turn.topic})"
        print(BOLD(tag))
        print(f"  user:  {turn.text}")
        try:
            data = send(turn)
            print(GREEN(f"  bot:   {short(data['reply'])}"))
            print(DIM(f"  via:   {data['model_used']}  prompt={data['prompt_version']}"))
        except httpx.HTTPStatusError as exc:
            print(RED(f"  ERROR: HTTP {exc.response.status_code}: {exc.response.text}"))
        except httpx.RequestError as exc:
            print(RED(f"  ERROR: cannot reach {BOT_URL} — is the service running?"))
            return 1
        print()
        time.sleep(0.8)

    print(BOLD("─" * 60))
    print(YELLOW(f"\nDone. Open the dashboard: {BOT_URL}/admin"))
    if ADMIN_TOKEN:
        print(f"Sign in with: {ADMIN_TOKEN}")
    else:
        print("Sign in with BOT_ADMIN_TOKEN from your .env.")

    print()
    print(BOLD("Memory-check expectations (turns 9-12):"))
    print("  9.  u_alice's reply mentions FRIDAY (the meeting day) — not")
    print("      daughter / appetite / scan results.")
    print(" 10.  u_sofia's reply mentions HER DAUGHTER — not work / appetite / scan.")
    print(" 11.  u_camille's reply mentions HER HUSBAND — not work / daughter / scan.")
    print(" 12.  u_ingrid's reply mentions THE DOCTOR'S CALL — not work / daughter / appetite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
