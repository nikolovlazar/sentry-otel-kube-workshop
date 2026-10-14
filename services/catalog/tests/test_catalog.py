import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
import pytest
from httpx import ASGITransport, AsyncClient
import app

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app.app), base_url="http://test") as client:
        yield client

@pytest.fixture
async def mocked_cache():
    entries = {}
    fake = AsyncMock()
    async def get(key): return entries.get(key)
    async def set_(key,value,**kwargs): entries[key] = value
    fake.get.side_effect = get
    fake.set.side_effect = set_
    with patch.object(app,"cache",fake): yield fake

async def test_cache_is_deterministic_and_isolated(client,mocked_cache):
    product = {"productId":"demo-widget","name":"Demo widget","unitPriceCents":1299}
    with patch.object(app,"read_product",AsyncMock(return_value=product)) as read:
        run = str(uuid4())
        a = await client.get("/v1/products/demo-widget",params={"run_id":run,"scenario":"cache-miss-hit"})
        b = await client.get("/v1/products/demo-widget",params={"run_id":run,"scenario":"cache-miss-hit"})
        c = await client.get("/v1/products/demo-widget",params={"run_id":str(uuid4()),"scenario":"cache-miss-hit"})
        assert [a.json()["cached"],b.json()["cached"],c.json()["cached"]] == [False,True,False]
        assert read.await_count == 2

async def test_slow_scenario_bypasses_cache(client,mocked_cache):
    with patch.object(app,"read_product",AsyncMock(return_value={"productId":"demo-widget"})) as read:
        response = await client.get("/v1/products/demo-widget",params={"run_id":str(uuid4()),"scenario":"slow-postgres"})
        assert response.status_code == 200
        read.assert_awaited_once_with(slow=True)
        mocked_cache.get.assert_not_awaited()

async def test_error_captured_once_and_returns_origin(client,mocked_cache):
    with patch.object(app,"read_product",AsyncMock(return_value={"productId":"demo-widget"})), patch.object(app.sentry_sdk,"capture_exception",return_value="event123") as capture:
        response = await client.get("/v1/products/demo-widget",params={"run_id":str(uuid4()),"scenario":"python-error"})
        assert response.status_code == 500
        assert response.json() == {"errorCode":"DEMO_CATALOG_ERROR","errorEventId":"event123","failureService":"catalog"}
        capture.assert_called_once()
        assert isinstance(capture.call_args.args[0],app.DemoCatalogError)

async def test_rejects_unbounded_fault_inputs(client):
    response = await client.get("/v1/products/demo-widget",params={"run_id":str(uuid4()),"scenario":"arbitrary-sql"})
    assert response.status_code == 422


def test_cache_span_describes_command_without_payload_or_run_id(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from telemetry import redis_request_hook
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    run_id = str(uuid4())
    with provider.get_tracer("test").start_as_current_span("SET") as span:
        redis_request_hook(span, None, ("SET", f"workshop:catalog:{run_id}:demo-widget", "private payload"), {})
    result, = exporter.get_finished_spans()
    assert result.name == "SET workshop:catalog"
    assert result.attributes["db.statement"] == "SET workshop:catalog:<scope>:demo-widget"
    assert result.attributes["db.system.name"] == "redis"
    assert "private payload" not in str(result.attributes)
    assert run_id not in str(result.attributes)
    provider.shutdown()


async def test_lock_timeout_bypasses_cache_and_captures_origin_once(client, mocked_cache):
    failure = app.psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")
    with patch.object(app, "read_product", AsyncMock(side_effect=failure)) as read, patch.object(app.sentry_sdk, "capture_exception", return_value="lock-event") as capture:
        response = await client.get("/v1/products/demo-widget", params={"run_id": str(uuid4()), "scenario": "postgres-lock-timeout"})
    assert response.status_code == 500
    assert response.json() == {"errorCode": "POSTGRES_LOCK_TIMEOUT", "errorEventId": "lock-event", "failureService": "catalog"}
    read.assert_awaited_once_with(lock_timeout=True)
    capture.assert_called_once_with(failure)
    mocked_cache.get.assert_not_awaited()
    mocked_cache.set.assert_not_awaited()


@pytest.fixture
def recording_tracer(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(app, "tracer", provider.get_tracer("catalog-tests"))
    yield provider.get_tracer("catalog-tests"), exporter
    provider.shutdown()


def test_postgres_context_contains_only_valid_current_span_ids(recording_tracer):
    from telemetry import postgres_application_name
    tracer, _ = recording_tracer
    assert postgres_application_name() == "workshop-catalog"
    with tracer.start_as_current_span("catalog.product_query") as span:
        ctx = span.get_span_context()
        name = postgres_application_name()
        assert name == f"w:{ctx.trace_id:032x}:{ctx.span_id:016x}"
        assert len(name.encode()) <= 63
    assert postgres_application_name() == "workshop-catalog"


async def test_lock_connections_use_business_context_and_release_on_error(recording_tracer, monkeypatch):
    _, exporter = recording_tracer
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/catalog-test")
    target, locker = AsyncMock(), AsyncMock()
    for conn in (target, locker):
        conn.__aenter__.return_value = conn
    failure = app.psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")
    async def execute(sql, *args):
        if sql.startswith("SELECT product_id"):
            raise failure
    target.execute.side_effect = execute
    with patch.object(app.psycopg.AsyncConnection, "connect", AsyncMock(side_effect=[target, locker])) as connect:
        with pytest.raises(app.psycopg.errors.LockNotAvailable):
            await app.read_product(lock_timeout=True)
    span, = exporter.get_finished_spans()
    expected = f"w:{span.context.trace_id:032x}:{span.context.span_id:016x}"
    assert [call.kwargs["application_name"] for call in connect.await_args_list] == [expected, expected]
    locker.rollback.assert_awaited_once()
    # Connection contexts receive the exception and therefore roll back/close.
    assert target.__aexit__.await_args.args[0] is app.psycopg.errors.LockNotAvailable
    assert locker.__aexit__.await_args.args[0] is app.psycopg.errors.LockNotAvailable
    assert span.status.status_code is app.StatusCode.ERROR


async def test_readiness_connection_is_untagged(client, mocked_cache, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/catalog-test")
    conn = AsyncMock()
    conn.__aenter__.return_value = conn
    with patch.object(app.psycopg.AsyncConnection, "connect", AsyncMock(return_value=conn)) as connect:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    connect.assert_awaited_once_with(os.environ["DATABASE_URL"], connect_timeout=2)
