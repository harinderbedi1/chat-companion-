"""Service classes — the business logic.

Each service owns one responsibility and is independently testable.
Services are constructed once in :mod:`companion.api.main` ``lifespan`` and
shared across requests via ``app.state``.
"""
