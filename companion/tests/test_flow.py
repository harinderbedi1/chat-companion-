"""End-to-end tests for the bot chat flow.

Tests use fakeredis and a fake LLM (see conftest.py). They cover:

* Authentication — wrong key returns 401.
* Authentication — int and string keys both accepted.
* Per-user history isolation — Alice's data never appears in Bob's reply.
* History delete endpoint wipes a user's data.
* Validation rejects oversized text.
* Streaming endpoint emits SSE chunks.
* Real HistoryService.delete_for_user works against Redis.
* session_id derivation is deterministic and unique.
"""

import pytest


# Shared payload skeleton — tests override user_id and text.
BASE_PAYLOAD = {
    "user_id": "u_test",
    "text": "hello",
    "category": {"title": "general"},
    "authorization_key": "test_secret_long_enough",
}


@pytest.mark.asyncio
async def test_unauthorized_request_returns_401(client):
    bad = {**BASE_PAYLOAD, "authorization_key": "wrong_secret"}
    response = await client.post("/bot/reply", json=bad)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_request_returns_200_with_reply(client):
    payload = {**BASE_PAYLOAD, "user_id": "u_solo", "text": "hi there"}
    response = await client.post("/bot/reply", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "u_solo"
    assert "echo:hi there" in body["reply"]
    assert body["model_used"] == "fake:primary"
    assert "request_id" in body


def test_authorization_key_coerces_int_to_str():
    """Platform may send authorization_key as a JSON number; we coerce to str."""
    from companion.api.schemas import ReplyRequest

    req = ReplyRequest(
        user_id="u1",
        text="hi",
        category={"title": "general"},
        authorization_key=12345678,    # JSON number, not a string
    )
    assert isinstance(req.authorization_key, str)
    assert req.authorization_key == "12345678"


def test_authorization_key_accepts_string_unchanged():
    from companion.api.schemas import ReplyRequest

    req = ReplyRequest(
        user_id="u1",
        text="hi",
        category={"title": "general"},
        authorization_key="abc_123",
    )
    assert req.authorization_key == "abc_123"


@pytest.mark.asyncio
async def test_two_users_do_not_share_history(client):
    """The core isolation guarantee: Alice's words never leak into Bob's reply."""
    alice = {**BASE_PAYLOAD, "user_id": "u_alice", "text": "I love pasta"}
    bob = {**BASE_PAYLOAD, "user_id": "u_bob", "text": "I love bread"}

    await client.post("/bot/reply", json=alice)
    await client.post("/bot/reply", json=bob)

    # Alice asks a follow-up — recalled history must only contain her own words.
    follow_up = {**BASE_PAYLOAD, "user_id": "u_alice", "text": "what did I say I loved?"}
    response = await client.post("/bot/reply", json=follow_up)
    assert response.status_code == 200
    reply = response.json()["reply"].lower()
    assert "pasta" in reply
    assert "bread" not in reply


@pytest.mark.asyncio
async def test_delete_history_wipes_user_data(client):
    """DELETE /bot/history/{user_id} removes everything for that user."""
    payload = {**BASE_PAYLOAD, "user_id": "u_to_delete", "text": "remember this"}
    await client.post("/bot/reply", json=payload)

    response = await client.delete(
        "/bot/history/u_to_delete",
        # Standard HTTP Authorization header carries the same shared secret.
        headers={"Authorization": "test_secret_long_enough"},
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == "u_to_delete"
    assert response.json()["deleted_keys"] >= 1

    # After deletion, a follow-up cannot recall anything.
    follow = {**BASE_PAYLOAD, "user_id": "u_to_delete", "text": "what did I say?"}
    follow_reply = (await client.post("/bot/reply", json=follow)).json()["reply"]
    assert "remember this" not in follow_reply


@pytest.mark.asyncio
async def test_validation_rejects_oversized_text(client):
    huge = {**BASE_PAYLOAD, "text": "x" * 9000}    # 8000 is the cap
    response = await client.post("/bot/reply", json=huge)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_health_and_ready_endpoints(client):
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200


@pytest.mark.asyncio
async def test_streaming_endpoint_returns_sse_chunks(client):
    payload = {**BASE_PAYLOAD, "user_id": "u_stream", "text": "hello there"}
    async with client.stream("POST", "/bot/reply/stream", json=payload) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = []
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                chunks.append(line)
    # The last event must be the SSE done marker.
    assert any("[DONE]" in c for c in chunks)
    assert "echo:" in "\n".join(chunks)


@pytest.mark.asyncio
async def test_real_history_service_deletes_user_rows_in_sqlite(tmp_path):
    """Exercise the production HistoryService.delete_for_user against SQLite."""
    import sqlite3
    from companion.config import Settings
    from companion.infra.db import init_db
    from companion.services.history import HistoryService
    from langchain_core.messages import HumanMessage

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    settings = Settings()
    service = HistoryService(db_path, settings)

    # Two users each get a message via the real history layer.
    service.for_session("u_alice").add_message(HumanMessage(content="hi from alice"))
    service.for_session("u_bob").add_message(HumanMessage(content="hi from bob"))

    deleted = await service.delete_for_user("u_alice")
    assert deleted == 1

    # Alice's rows are gone, Bob's are untouched — the storage-layer isolation guarantee.
    with sqlite3.connect(db_path) as conn:
        alice_rows = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", ("u_alice",),
        ).fetchone()[0]
        bob_rows = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", ("u_bob",),
        ).fetchone()[0]
    assert alice_rows == 0
    assert bob_rows == 1


def test_session_id_derivation_is_unique_per_user():
    """The pure-function check: distinct user_ids never share a session_id."""
    from companion.services.history import HistoryService

    assert HistoryService.session_id("u_alice") == "u_alice"
    assert HistoryService.session_id("u_bob") == "u_bob"

    ids = [HistoryService.session_id(u) for u in ("u_a", "u_b", "u_c", "u_d")]
    assert len(set(ids)) == len(ids), f"session_id collision: {ids}"


def test_user_text_is_prefixed_with_timestamp_when_provided():
    """LLMService prefixes the user message with a human-readable timestamp."""
    from companion.services.llm import LLMService

    # 2026-05-14 10:30 UTC = 1778753400 epoch seconds
    prefixed = LLMService._format_user_text("hello", 1778753400)
    assert prefixed.startswith("[2026-05-14")
    assert "UTC]" in prefixed
    assert prefixed.endswith("hello")


def test_user_text_is_unchanged_when_no_timestamp():
    from companion.services.llm import LLMService

    assert LLMService._format_user_text("hello", None) == "hello"


@pytest.mark.asyncio
async def test_request_with_timestamp_returns_200(client):
    """End-to-end: timestamp is accepted and the request flows through."""
    payload = {
        **BASE_PAYLOAD,
        "user_id": "u_ts",
        "text": "what time is it for me?",
        "timestamp": 1778753400,
    }
    response = await client.post("/bot/reply", json=payload)
    assert response.status_code == 200


# ── Admin dashboard ──────────────────────────────────────────────────────


ADMIN_HEADERS = {"Authorization": "Bearer admin_test_token"}


@pytest.mark.asyncio
async def test_admin_endpoints_reject_unauthenticated_calls(client):
    """No token / wrong token → 401."""
    assert (await client.get("/admin/stats")).status_code == 422   # missing header
    assert (await client.get("/admin/stats",
        headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.get("/admin/users",
        headers={"Authorization": "Bearer wrong"})).status_code == 401


@pytest.mark.asyncio
async def test_admin_stats_returns_zero_initially(client):
    response = await client.get("/admin/stats", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["total_messages"] == 0
    assert body["safety_rejected"] == 0


@pytest.mark.asyncio
async def test_admin_stats_increments_on_successful_reply(client):
    payload = {**BASE_PAYLOAD, "user_id": "u_counter", "text": "hello"}
    await client.post("/bot/reply", json=payload)
    await client.post("/bot/reply", json=payload)

    response = await client.get("/admin/stats", headers=ADMIN_HEADERS)
    body = response.json()
    assert body["total_messages"] == 2
    assert body["messages_today"] == 2


@pytest.mark.asyncio
async def test_admin_users_list_includes_active_users(client):
    """After two users chat, they should both appear in /admin/users."""
    for user in ("u_admin_a", "u_admin_b"):
        await client.post("/bot/reply", json={**BASE_PAYLOAD, "user_id": user, "text": "hi"})

    response = await client.get("/admin/users", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    user_ids = {u["user_id"] for u in body["users"]}
    assert "u_admin_a" in user_ids
    assert "u_admin_b" in user_ids
    a = next(u for u in body["users"] if u["user_id"] == "u_admin_a")
    assert a["message_count"] == 1
    assert a["last_seen"] is not None


@pytest.mark.asyncio
async def test_admin_user_detail_returns_chat_messages(client):
    """Send a message, then read it back from the admin endpoint."""
    payload = {**BASE_PAYLOAD, "user_id": "u_detail", "text": "hi there"}
    await client.post("/bot/reply", json=payload)

    response = await client.get("/admin/users/u_detail", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "u_detail"
    # 2 rows: the user's message and the bot's reply (the fake LLM echoes).
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "human"
    assert body["messages"][0]["content"] == "hi there"
    assert body["messages"][1]["role"] == "ai"
    assert "echo:hi there" in body["messages"][1]["content"]


@pytest.mark.asyncio
async def test_admin_html_page_is_served(client):
    """GET /admin (no auth) returns the HTML page; auth is on the API."""
    response = await client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Bot Admin</title>" in response.text


@pytest.mark.asyncio
async def test_summary_service_compacts_history_past_threshold(tmp_path):
    """When history grows past the threshold, SummaryService rewrites it."""
    from companion.config import Settings
    from companion.infra.db import init_db
    from companion.services.history import HistoryService
    from companion.services.summary import SummaryService
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    db_path = str(tmp_path / "summary.db")
    init_db(db_path)
    settings = Settings()
    history_service = HistoryService(db_path, settings)

    # Pre-populate 12 messages — enough to trigger the default threshold of 10.
    session_id = history_service.session_id("u_summary")
    history = history_service.for_session(session_id)
    for i in range(6):
        history.add_message(HumanMessage(content=f"user msg {i}"))
        history.add_message(AIMessage(content=f"bot reply {i}"))
    assert len(history.messages) == 12

    # Fake AI that returns a fixed summary string for the summarizer call.
    class _FakeSummarizerModel:
        async def ainvoke(self, prompt):
            class _Resp:
                content = "User discussed several topics and asked questions."
            return _Resp()

    summary = SummaryService(settings, history_service, lambda: _FakeSummarizerModel())
    await summary.maybe_compact(session_id)

    # After compacting: 1 summary + keep_recent (default 2) = 3 messages.
    new_messages = history.messages
    assert len(new_messages) == 3
    assert isinstance(new_messages[0], SystemMessage)
    assert SummaryService.SUMMARY_MARKER in new_messages[0].content
    # The tail is the last raw pair.
    assert isinstance(new_messages[1], HumanMessage)
    assert new_messages[1].content == "user msg 5"
    assert isinstance(new_messages[2], AIMessage)
    assert new_messages[2].content == "bot reply 5"


@pytest.mark.asyncio
async def test_summary_service_skips_short_histories(tmp_path):
    """Histories below the threshold are left alone."""
    from companion.config import Settings
    from companion.infra.db import init_db
    from companion.services.history import HistoryService
    from companion.services.summary import SummaryService
    from langchain_core.messages import AIMessage, HumanMessage

    db_path = str(tmp_path / "short.db")
    init_db(db_path)
    settings = Settings()
    history_service = HistoryService(db_path, settings)
    session_id = history_service.session_id("u_short")
    history = history_service.for_session(session_id)
    history.add_message(HumanMessage(content="hello"))
    history.add_message(AIMessage(content="hi back"))

    summary = SummaryService(settings, history_service, lambda: None)
    await summary.maybe_compact(session_id)

    # Untouched.
    assert len(history.messages) == 2
