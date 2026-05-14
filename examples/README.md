# Examples — running the service end-to-end

Two things in this folder:

| File | Purpose |
| --- | --- |
| `env.cerebras.example` | Drop-in `.env` config for using Cerebras (GPT-OSS-120B). |
| `two_user_chat.py` | Standalone script that talks to the running service as two users in two languages, then tells you how to inspect the result in the admin dashboard. |

---

## Quick start without Docker (using Cerebras + GPT-OSS-120B)

This walks you through running the whole service on your own machine,
end-to-end, with Cerebras as the AI provider.

### Step 1 — (no Redis needed)

Storage is **SQLite**, a single file on disk. No server to install, no
port to open. The file is created the first time the service starts.
Default location is `./companion.db` in the service directory.

### Step 2 — Set up Python and install the service

From the `service/` directory:

```bash
cd service

# Create + activate a venv
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# (on Windows: .venv\Scripts\activate)

# Install dependencies + the companion package in editable mode
pip install -r requirements.txt
pip install -e .
```

### Step 3 — Configure with Cerebras

Copy the Cerebras-specific example into the real `.env`:

```bash
cp examples/env.cerebras.example .env
```

Open `.env` and fill in three values:

1. **`BOT_SHARED_SECRET`** — any long random string. Generate one with:
   ```bash
   openssl rand -hex 32
   ```
   (The platform team will need this same value to send us requests.)

2. **`BOT_ADMIN_TOKEN`** — a *different* long random string, for signing
   in to the dashboard.

3. **`CEREBRAS_API_KEY`** — your Cerebras API key (starts with `csk-…`).
   Get one at https://cloud.cerebras.ai/.

The `BOT_LLM_PRIMARY` line is already set to `cerebras:gpt-oss-120b`.

### Step 4 — Run the service

```bash
uvicorn companion.api.main:app --port 8765 --reload
```

You should see startup logs like:
```
INFO   startup llm_primary=cerebras:gpt-oss-120b ...
```

Confirm the service is up:
```bash
curl http://localhost:8765/health
# {"status":"ok"}

curl http://localhost:8765/ready
# {"status":"ready"}
```

### Step 5 — Run the two-user test script

In a **second terminal** (leave the service running in the first):

```bash
cd service
source .venv/bin/activate

# The script reads BOT_SHARED_SECRET and BOT_ADMIN_TOKEN from your env.
# Easiest is to source them from .env:
export $(grep -v '^#' .env | xargs)

python examples/two_user_chat.py
```

You'll see the bot reply in real time to each of 7 messages — Alice in
English, Bob in Spanish, with two memory-check turns.

When it finishes, the script prints the exact steps to view the same
conversation in the admin dashboard.

### Step 6 — View the admin dashboard

In a browser:
```
http://localhost:8765/admin
```

Sign in with your `BOT_ADMIN_TOKEN`. You should see:

- **Stat cards**: 7 total messages, 7 today, 0 safety-rejected.
- **User list**: `u_alice_demo` and `u_bob_demo`.
- **Click each user** → their full conversation, in their language,
  with timestamps.

Or fetch via curl (replace `<your_admin_token>`):

```bash
curl -H "Authorization: Bearer <your_admin_token>" \
     http://localhost:8765/admin/stats

curl -H "Authorization: Bearer <your_admin_token>" \
     http://localhost:8765/admin/users

curl -H "Authorization: Bearer <your_admin_token>" \
     http://localhost:8765/admin/users/u_alice_demo
```

---

## Running the test script again later

Once you've gone through the setup above, the daily-use loop is just:

```bash
# Terminal 1 — start the service
cd service
source .venv/bin/activate
uvicorn companion.api.main:app --port 8765 --reload

# Terminal 2 — run tests
cd service
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
python examples/two_user_chat.py
```

Want a clean slate before re-running? The script prints two curl
commands at the end that delete `u_alice_demo` and `u_bob_demo`'s
chat history. Or wipe the whole database file:

```bash
rm companion.db          # then restart the service to recreate it
```

---

## Customizing the script

`two_user_chat.py` has a `CONVERSATION` list at the top. Edit it to
add more turns, different users, different languages, or different
categories. Each turn is just:

```python
Turn(user_id="u_xxx",
     category_id="cat_xxx",
     category_title="...",
     language="en",      # or "es", "fr", "de", "hi", ...
     text="the message")
```

The script will pace itself with a 300ms gap between messages so the
service's logs stay readable in real time.

---

## What about with Docker?

`docker compose up --build` from the service root brings up the service
with the SQLite file mounted on a persistent volume (`companion-data`).
The test script works the same way — just point `BOT_URL` at wherever
the Dockerized service is listening (defaults to `http://localhost:8765`).

---

## Trouble?

| Symptom | Likely fix |
| --- | --- |
| `ERROR: cannot reach http://localhost:8765` | Service isn't running — start it in terminal 1. |
| `HTTP 401 unauthorized` | `BOT_SHARED_SECRET` in your env doesn't match what's in `.env`. |
| `HTTP 503 ai_unavailable` | Cerebras API key wrong/missing, OR Cerebras rate-limited your key. |
| `HTTP 503 db_unavailable` on `/ready` | The SQLite file can't be opened — usually a permissions issue on its directory. |
| Bot replies are empty / very short | Most likely the moderation step is rejecting them. Set `BOT_MODERATION_PROVIDER=none` in `.env` during testing. |
| Slow first reply | First-call cold start; subsequent calls are fast. |
