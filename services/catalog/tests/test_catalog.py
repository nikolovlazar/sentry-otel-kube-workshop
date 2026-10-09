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
