"""Real PostgreSQL checks; opt in with an isolated CATALOG_TEST_DATABASE_URL."""
import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app


@pytest.fixture
async def postgres_database(monkeypatch):
    database_url = os.getenv("CATALOG_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set CATALOG_TEST_DATABASE_URL to run isolated PostgreSQL integration checks")
    assert urlparse(database_url).path == "/workshop_catalog_test", "Only the dedicated test database is allowed"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(app, "tracer", provider.get_tracer("catalog-postgres-integration"))
    connect = app.psycopg.AsyncConnection.connect
    async with await connect(database_url, autocommit=True) as conn:
        await conn.execute((Path(__file__).parents[1] / "migrations.sql").read_text())
    yield database_url, connect, exporter
    provider.shutdown()


async def test_real_timeout_links_business_span_and_releases_both_connections(postgres_database):
    database_url, connect, exporter = postgres_database
    connections = []
    async def tracked_connect(*args, **kwargs):
        conn = await connect(*args, **kwargs)
        connections.append((conn, conn.info.parameter_status("application_name")))
        return conn
    started = time.monotonic()
    with patch.object(app.psycopg.AsyncConnection, "connect", tracked_connect), patch.object(app, "cache", AsyncMock()) as cache, patch.object(app.sentry_sdk, "capture_exception", return_value="real-lock-event") as capture:
        async with AsyncClient(transport=ASGITransport(app=app.app), base_url="http://test") as client:
            response = await client.get("/v1/products/demo-widget", params={"run_id": str(uuid4()), "scenario": "postgres-lock-timeout"})
    elapsed = time.monotonic() - started
    assert .70 <= elapsed < 1.8
    assert response.status_code == 500
    assert response.json() == {"failureService": "catalog", "errorCode": "POSTGRES_LOCK_TIMEOUT", "errorEventId": "real-lock-event"}
    capture.assert_called_once()
    assert isinstance(capture.call_args.args[0], app.psycopg.errors.LockNotAvailable)
    cache.get.assert_not_awaited()
    cache.set.assert_not_awaited()
    assert len(connections) == 2 and all(conn.closed for conn, _ in connections)
    business_span, = [span for span in exporter.get_finished_spans() if span.name == "catalog.product_query"]
    expected = f"w:{business_span.context.trace_id:032x}:{business_span.context.span_id:016x}"
    assert [name for _, name in connections] == [expected, expected]
    assert business_span.status.status_code is app.StatusCode.ERROR
    started = time.monotonic()
    product = await app.read_product()
    assert product["productId"] == "demo-widget" and time.monotonic() - started < .5
    async with await connect(database_url, autocommit=True) as observer:
        cursor = await observer.execute("SELECT count(*) FROM catalog.products")
        assert (await cursor.fetchone())[0] == 1


async def test_cancelled_query_releases_lock_without_waiting_for_watchdog(postgres_database):
    database_url, connect, _ = postgres_database
    connections = []
    async def tracked_connect(*args, **kwargs):
        conn = await connect(*args, **kwargs)
        connections.append(conn)
        return conn
    async with await connect(database_url, autocommit=True) as observer:
        with patch.object(app.psycopg.AsyncConnection, "connect", tracked_connect):
            task = asyncio.create_task(app.read_product(lock_timeout=True))
            try:
                deadline = time.monotonic() + .5
                while time.monotonic() < deadline:
                    cursor = await observer.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'")
                    if (await cursor.fetchone())[0]:
                        break
                    await asyncio.sleep(.01)
                else:
                    pytest.fail("Product query never waited on the table lock")
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
        assert len(connections) == 2 and all(conn.closed for conn in connections)
        started = time.monotonic()
        assert (await app.read_product())["productId"] == "demo-widget"
        assert time.monotonic() - started < .5


async def test_idle_transaction_watchdog_releases_interrupted_locker(postgres_database):
    database_url, connect, _ = postgres_database
    locker_pid = None
    async def tracked_connect(*args, **kwargs):
        nonlocal locker_pid
        conn = await connect(*args, **kwargs)
        locker_pid = conn.info.backend_pid
        return conn
    async with await connect(database_url, autocommit=True) as observer:
        with patch.object(app.psycopg.AsyncConnection, "connect", tracked_connect):
            with pytest.raises(app.psycopg.errors.IdleInTransactionSessionTimeout):
                async with app.catalog_table_lock(True, "workshop-catalog-test"):
                    await asyncio.sleep(2.25)
                    cursor = await observer.execute("SELECT count(*) FROM pg_stat_activity WHERE pid=%s", (locker_pid,))
                    assert (await cursor.fetchone())[0] == 0
        assert (await app.read_product())["productId"] == "demo-widget"
