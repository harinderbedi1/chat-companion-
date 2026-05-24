# Companion — Chat Service

A small, provider-agnostic chat service with per-user conversation
memory. Tuned for a private women-only community for cancer patients.

You can run it with or without Docker. The AI provider is your choice —
**Cerebras** (default — very fast Llama hosting), **OpenAI**,
**Anthropic**, or fully local **Ollama**. Switching providers is a
one-line change in `.env`. No code changes.

Storage is **SQLite** (a single file). No Redis, no Postgres, no server
to install.

---

## Step 1 — Get the dependencies in

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\activate             # Windows

pip install -r requirements.txt
pip install -e .
```

(Or skip this and use Docker — see "With Docker" below.)

---

## Step 2 — Set up your `.env` file

This is the part that trips most people up. Read this section even if
you skip everything else.

```bash
cp .env.example .env
```

Now open `.env` and fill in three values. **You make these up yourself**
— they are NOT obtained from any third party. Generate them with:

```bash
openssl rand -hex 32
```

Run that command twice and copy the two random strings into `.env`:

### `BOT_SHARED_SECRET`

The password the platform sends with every chat request. The platform
team needs to know this same value on their side. Anything long and
random works.

```env
# example shape — generate your own with `openssl rand -hex 32`
BOT_SHARED_SECRET=<64-char-hex-string-you-generated>
```

### `BOT_ADMIN_TOKEN`

Your password for signing into the admin dashboard. **You pick this**
— it is not fetched from anywhere. Make it different from
`BOT_SHARED_SECRET`. If you leave this empty, the admin dashboard
returns 503.

```env
# example shape — generate your own with `openssl rand -hex 32`
BOT_ADMIN_TOKEN=<a-different-64-char-hex-string>
```

### Your AI provider's API key

The default in `.env.example` is **Cerebras**. Get a free key at
https://cloud.cerebras.ai/ and paste it:

```env
BOT_LLM_PRIMARY=cerebras:gpt-oss-120b
BOT_LLM_FALLBACK=cerebras:gpt-oss-120b
CEREBRAS_API_KEY=csk-...
```

> **Note on Cerebras model names** — they add and retire models often.
> Check https://inference-docs.cerebras.ai/api-reference/models for
> the current list. The example above uses `gpt-oss-120b` (OpenAI's
> open-source GPT model hosted on Cerebras).

Want a different provider? See [Picking your AI provider](#picking-your-ai-provider)
further down — there are commented blocks for OpenAI, Anthropic, and
local Ollama in `.env.example`.

### One more thing — moderation

```env
BOT_MODERATION_PROVIDER=none
```

Leave as `none` if you only have a Cerebras key. The default screens
the AI's replies through OpenAI's free moderation API; that requires an
`OPENAI_API_KEY` too. Setting `none` skips that check (fine for dev,
not recommended for production).

---

## Step 3 — Start the service

```bash
.venv/bin/uvicorn companion.api.main:app --port 8765 --reload
```

You should see:
```
INFO   startup db_path=./companion.db llm_primary=cerebras:gpt-oss-120b ...
INFO   Application startup complete.
```

In another terminal, confirm it's alive:
```bash
curl http://localhost:8765/health
# {"status":"ok"}

curl http://localhost:8765/ready
# {"status":"ready"}
```

The SQLite database file `companion.db` was just created in the
repo root.

---

## Step 4 — Send a test message

```bash
curl -X POST http://localhost:8765/bot/reply \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u_test",
    "text": "Hello, this is my first message.",
    "category": { "id": "cat_general", "title": "general" },
    "authorization_key": "YOUR_BOT_SHARED_SECRET_HERE"
  }'
```

Replace `YOUR_BOT_SHARED_SECRET_HERE` with the value you set above.

Expected response:
```json
{
  "user_id": "u_test",
  "reply": "Hi there — I'm here to listen...",
  "model_used": "cerebras:gpt-oss-120b",
  "prompt_version": "v2",
  "request_id": "..."
}
```

---

## Step 5 — Run the multi-user test script

A scripted conversation: two users (Alice and Bob), two languages
(English and Spanish), with memory checks built in.

```bash
# Load .env into your shell so the script can read BOT_SHARED_SECRET etc.
export $(grep -v '^#' .env | xargs)

.venv/bin/python examples/two_user_chat.py
```

You'll see 7 turns of conversation print out. The script ends by
telling you exactly how to view the same conversation in the admin
dashboard.

---

## Step 6 — Open the admin dashboard

In your browser:

```
http://localhost:8765/admin
```

A login screen appears. Paste your `BOT_ADMIN_TOKEN` (the value you
set in step 2). Click "Sign in".

You should see:

- **Top cards:** how many messages total, today, last 7 days, plus how
  many were rejected by safety.
- **User list:** every user who has chatted. After running the test
  script, you'll see `u_alice_demo` and `u_bob_demo`.
- **Click a user → their full chat history**, with timestamps,
  alternating styled blocks for human vs bot messages.

The token is saved in your browser's localStorage so you stay signed in
across reloads. Click "Sign out" to clear it.

### Or query the admin API directly

```bash
curl -H "Authorization: Bearer YOUR_BOT_ADMIN_TOKEN" \
     http://localhost:8765/admin/stats

curl -H "Authorization: Bearer YOUR_BOT_ADMIN_TOKEN" \
     http://localhost:8765/admin/users

curl -H "Authorization: Bearer YOUR_BOT_ADMIN_TOKEN" \
     http://localhost:8765/admin/users/u_alice_demo
```

---

## Environment variables reference

Every setting the service reads. Defaults shown in parens. **Required** items
must be set or the service won't start.

### Identity & auth

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_SHARED_SECRET` | **required** | The shared secret the platform sends in every chat request (in `authorization_key` body field). Generate with `openssl rand -hex 32`. Comma-separated values are accepted for zero-downtime rotation. |
| `BOT_ADMIN_TOKEN` | **required for `/admin`** | Your password for signing in to the admin dashboard. Different long random string. If unset, the dashboard returns 503. |
| `BOT_AUTH_MODE` | `body` | How the platform's secret is sent. `body` = inside the JSON body's `authorization_key`. `hmac_header` = HMAC-SHA256 signature in `X-Signature-Sha256` header over the raw body. |
| `BOT_PLATFORM_NAME` | `Platform` | The name of the platform that's hosting this bot. Injected into the AI's system prompt (e.g. "You are a presence on `{platform_name}`"). Mostly cosmetic. |
| `BOT_PROMPT_VERSION` | `v4` | A string tag for the current prompt iteration. Logged on every reply and shown in the response, so quality changes can be correlated to prompt rollouts. Bump it whenever you edit `companion/prompts/system.py`. |

### Storage

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_DB_PATH` | `./companion.db` | Path to the SQLite database file. Created on first run. Inside Docker it's overridden to `/data/companion.db` (persistent volume). Use `:memory:` for an ephemeral test database. |

### AI provider

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_LLM_PRIMARY` | **required** | The main AI to call, in `provider:model` form (e.g. `cerebras:gpt-oss-120b`, `openai:gpt-4o`, `anthropic:claude-sonnet-4-6`, `ollama:llama3.1:70b-instruct`). |
| `BOT_LLM_FALLBACK` | **required** | The backup AI, same format. Used automatically if the primary errors/times out. Pick a different vendor for resilience. |
| `BOT_LLM_PRIMARY_BASE_URL` | unset | Custom HTTP endpoint for the primary model. Only needed for self-hosted setups (Ollama on `http://localhost:11434`, vLLM, OpenAI-compatible hosts). |
| `BOT_LLM_FALLBACK_BASE_URL` | unset | Same idea for the fallback model. |
| `BOT_LLM_TIMEOUT_SECONDS` | `30` | How long we'll wait for a single LLM call to complete before giving up and falling back. Bump higher if you're seeing premature timeouts with slow models. |
| `BOT_MAX_OUTPUT_TOKENS` | `800` | The hard cap on how many tokens (≈ words/punctuation) the AI may produce per reply. Higher = longer replies + more cost. |
| `BOT_TEMPERATURE` | `0.5` | How random the AI's output is. 0 = deterministic (same input → same output). 1 = creative. 0.5 is a balanced default for supportive chat. |

### AI provider API keys (set the ones you use)

| Variable | Where to get it |
| --- | --- |
| `CEREBRAS_API_KEY` | https://cloud.cerebras.ai/ |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/ |

### Conversation history

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_HISTORY_TTL_SECONDS` | `2592000` (30 days) | How long a user's chat history is retained. Currently informational; the service does not auto-prune (rows live until `DELETE /bot/history/{user_id}` is called). |
| `BOT_HISTORY_SUMMARIZE_THRESHOLD` | `10` | When a user's stored message count reaches this number, the older messages are condensed into a single summary by the AI. Keeps prompts short on long conversations. Higher = richer context but pricier calls. Lower = cheaper but the AI sees less raw history. |
| `BOT_HISTORY_KEEP_RECENT` | `2` | When summarization fires, this many of the most recent messages are kept verbatim (not summarized). The current exchange's immediate context isn't lost. |

### Reply safety

These bound what the bot is allowed to return to the user.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_MIN_REPLY_CHARS` | `5` | Replies shorter than this are rejected with `422 reply_rejected`. Catches the AI returning empty or single-character output. |
| `BOT_MAX_REPLY_CHARS` | `2000` | Replies longer than this are rejected. Catches the AI rambling. |
| `BOT_MODERATION_PROVIDER` | `openai` | How the bot's reply is screened before being returned. **This is the safety check that happens AFTER the AI replies but BEFORE the user sees it.** Options: |

`BOT_MODERATION_PROVIDER` options explained:

- **`openai`** — Calls OpenAI's free `omni-moderation-latest` API. Catches harassment, self-harm encouragement, sexual content involving minors, etc. Requires `OPENAI_API_KEY` to be set even if your main LLM provider is Cerebras/Anthropic/Ollama. **Recommended for production.**
- **`llama_guard`** — Calls a local Llama Guard 3 endpoint. Useful in fully air-gapped / no-third-party deployments.
- **`none`** — Skips the moderation step entirely. **Only use in development.** With this, a misbehaving prompt or model could let unsafe replies through.

### Observability (all optional)

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_LOG_LEVEL` | `INFO` | Logging verbosity. `DEBUG` for noisy investigation, `WARNING` for production. |
| `BOT_LANGFUSE_HOST` | unset | Self-hosted Langfuse URL. If set, every AI call's prompt + response is sent to Langfuse for inspection. |
| `BOT_LANGFUSE_PUBLIC_KEY` | unset | Langfuse public key. |
| `BOT_LANGFUSE_SECRET_KEY` | unset | Langfuse secret key. |

---

## API reference

### `POST /bot/reply`

Request body:

| Field | Required | Type | Description |
| --- | --- | --- | --- |
| `user_id` | yes | string or number | Stable per user. Used as the key for chat history. |
| `text` | yes | string (1–8000 chars) | The user's message. |
| `category` | yes | object | Topic context — see below. |
| `category.title` | yes | string (≤128) | Topic name. Injected into the AI prompt. |
| `category.id` | recommended | string or number | Stable category identifier. Used for per-category stats. |
| `authorization_key` | yes | string or number | Shared secret matching `BOT_SHARED_SECRET`. |
| `timestamp` | recommended | int (epoch seconds) | When the user sent the message. Prefixed into history so the AI can reason about time gaps. |

> String-or-number fields: send whichever type your system already
> produces — we normalize to string internally.

Response on success (`200`):
```json
{
  "user_id": "u_alice",
  "reply": "Here's what I'm hearing...",
  "model_used": "cerebras:gpt-oss-120b",
  "prompt_version": "v2",
  "request_id": "8f3b1c..."
}
```

Errors:

| Status | When |
| --- | --- |
| `401` | `authorization_key` doesn't match the configured secret. |
| `422` | Body failed Pydantic validation, OR our safety check rejected the reply. |
| `503` | Both AI providers down. Body includes a `retry_after` hint. |

### `POST /bot/reply/stream`

Same request as `/bot/reply`. Returns Server-Sent Events — one `data:`
event per token chunk, ending with `data: [DONE]`.

### `DELETE /bot/history/{user_id}`

Wipes a user's chat history (GDPR). No body. Header
`Authorization: <BOT_SHARED_SECRET>`.

Response:
```json
{ "user_id": "u_alice", "deleted_keys": 12 }
```

### `GET /admin`

Single-page admin UI (Tailwind + vanilla JS). Login form asks for
`BOT_ADMIN_TOKEN`.

### `GET /admin/stats` · `GET /admin/users` · `GET /admin/users/{user_id}`

JSON admin APIs. All require `Authorization: Bearer <BOT_ADMIN_TOKEN>`.

### `GET /health` · `GET /ready`

Liveness + readiness probes. No auth.

### `GET /docs`

Auto-generated OpenAPI / Swagger UI for the live service.

---

## With Docker (alternative to steps 1 and 3)

If you'd rather not set up Python locally:

```bash
# From the repo root, after setting up your .env (step 2):
docker compose up --build

# Stop:
docker compose down          # data persists in a named volume
docker compose down -v       # wipe the database too
```

The Dockerfile installs everything and starts uvicorn. The SQLite file
lives on the `companion-data` volume so it survives restarts.

---

## Picking your AI provider

Edit `.env`. Set `BOT_LLM_PRIMARY` and `BOT_LLM_FALLBACK`. Add the
matching API key on a separate line. Restart the service.

### Cerebras (default — fastest Llama inference)
```env
BOT_LLM_PRIMARY=cerebras:gpt-oss-120b
BOT_LLM_FALLBACK=cerebras:gpt-oss-120b
CEREBRAS_API_KEY=csk-...
```
Get a key at https://cloud.cerebras.ai/. Model list at
https://inference-docs.cerebras.ai/api-reference/models.

### OpenAI
```env
BOT_LLM_PRIMARY=openai:gpt-4o
BOT_LLM_FALLBACK=openai:gpt-4o-mini
OPENAI_API_KEY=sk-...
```
Get a key at https://platform.openai.com/api-keys.

### Anthropic (Claude) with OpenAI fallback
```env
BOT_LLM_PRIMARY=anthropic:claude-sonnet-4-6
BOT_LLM_FALLBACK=openai:gpt-4o-mini
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

### Cerebras primary + OpenAI fallback (recommended for production)
```env
BOT_LLM_PRIMARY=cerebras:gpt-oss-120b
BOT_LLM_FALLBACK=openai:gpt-4o-mini
CEREBRAS_API_KEY=csk-...
OPENAI_API_KEY=sk-...
```
Fast on the happy path; well-tested fallback if Cerebras has issues.

### Fully local (Ollama, no API keys)
```env
BOT_LLM_PRIMARY=ollama:llama3.1:70b-instruct
BOT_LLM_FALLBACK=ollama:llama3.1:8b-instruct
BOT_LLM_PRIMARY_BASE_URL=http://localhost:11434
BOT_LLM_FALLBACK_BASE_URL=http://localhost:11434
```
Install Ollama first: https://ollama.com/. Then
`ollama pull llama3.1:70b-instruct`.

---

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/bot/reply` | Main chat endpoint. |
| `POST` | `/bot/reply/stream` | Same input, streams the reply as SSE. |
| `DELETE` | `/bot/history/{user_id}` | Wipe a user's chat history (GDPR). Header `Authorization: <shared_secret>`. |
| `GET` | `/admin` | Admin dashboard UI (token-gated). |
| `GET` | `/admin/stats` | JSON stats for the dashboard. |
| `GET` | `/admin/users` | List of users with counts. |
| `GET` | `/admin/users/{user_id}` | One user's full chat history. |
| `GET` | `/health` | Liveness probe. |
| `GET` | `/ready` | Readiness probe (checks SQLite). |
| `GET` | `/docs` | Auto-generated OpenAPI/Swagger UI. |

---

## Running the tests

```bash
.venv/bin/pytest
```

22 tests, all run in under a second. No external services needed — each
test gets its own temp SQLite file.

---

## Troubleshooting

| Symptom | Likely fix |
| --- | --- |
| `HTTP 401 unauthorized` from `/bot/reply` | The `authorization_key` you sent doesn't match `BOT_SHARED_SECRET` in your `.env`. |
| `HTTP 503 ai_unavailable` | Wrong API key, or Cerebras/OpenAI rate-limited you, or the model name was deprecated. |
| `HTTP 503 db_unavailable` | SQLite can't write to the path in `BOT_DB_PATH`. Check directory permissions. |
| `/admin` returns 503 | `BOT_ADMIN_TOKEN` is empty in `.env`. Set it and restart. |
| Login fails on `/admin` | The token you typed doesn't match `BOT_ADMIN_TOKEN`. Check `.env`. |
| Bot replies are empty / very short | Most likely the safety/moderation check is rejecting them. Try `BOT_MODERATION_PROVIDER=none` during testing. |
| Tests fail to import | Did you run `pip install -e .`? That installs the `companion` package into your venv. |

---

## Where things live

| File / folder | Contains |
| --- | --- |
| `.env.example` | Template for `.env`. Heavily commented — read it. |
| `companion/config.py` | All env-var → typed Settings. |
| `companion/api/routes.py` | The HTTP endpoints. |
| `companion/api/main.py` | FastAPI app + startup (lifespan). |
| `companion/services/` | Business logic, one file per service class. |
| `companion/prompts/system.py` | The system prompt. |
| `companion/admin/index.html` | The admin dashboard page. |
| `companion/tests/` | 22 end-to-end tests. |
| `examples/` | Standalone test script + Cerebras env example. |
| `docker-compose.yml` | One-command service start. |

For the architecture and design rationale, see the docs repo (separate).

---

## Switching providers later

Edit `.env`. Change `BOT_LLM_PRIMARY` and `BOT_LLM_FALLBACK`. Make sure
the corresponding API key is set. Restart the service. Done.

No code change. No rebuild. No data migration.
