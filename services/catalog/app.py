"""A synthetic product service with deterministic cache, latency and error demos."""
import json
import os
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from telemetry import configure, emit, postgres_application_name, workflow_context
provider = configure()

import psycopg
import redis.asyncio as redis
import sentry_sdk
from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.utils import suppress_instrumentation

tracer = trace.get_tracer("workshop.catalog")
cache = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

Scenario = Literal["full-journey", "cache-miss-hit", "slow-postgres", "postgres-lock-timeout", "python-error", "go-error", "async-worker", "next-error", "browser-error"]
CachePolicy = Literal["default", "fresh", "bypass"]


class DemoCatalogError(RuntimeError):
    pass


@asynccontextmanager
async def lifespan(app):
    emit("Catalog started", **{"workshop.step": "startup"})
    yield
    await cache.aclose()
    sentry_sdk.flush(timeout=2)
    provider.shutdown()


app = FastAPI(title="Workshop catalog", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready")


@asynccontextmanager
async def catalog_table_lock(enabled, application_name):
    if not enabled:
        yield
        return
    async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], application_name=application_name) as locker:
        try:
            # Both lock acquisition and an interrupted holder are bounded.
            await locker.execute("SET LOCAL lock_timeout = '750ms'")
            await locker.execute("SET LOCAL idle_in_transaction_session_timeout = '2s'")
            await locker.execute("LOCK TABLE catalog.products IN ACCESS EXCLUSIVE MODE")
            yield
        finally:
            # BaseException (including cancellation) also exits the connection
            # context, which rolls back and closes it even if rollback fails.
            await locker.rollback()


async def read_product(slow=False, lock_timeout=False):
    # The driver emits one CLIENT span per executed SQL statement; this is
    # the business operation containing connection, queries and row decoding.
    with tracer.start_as_current_span("catalog.product_query"):
        application_name = postgres_application_name()
        async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], application_name=application_name) as conn:
            async with catalog_table_lock(lock_timeout, application_name):
                if lock_timeout:
                    await conn.execute("SET LOCAL lock_timeout = '750ms'")
                if slow: await conn.execute("SELECT pg_sleep(1.2)")
                cursor = await conn.execute("SELECT product_id,name,unit_price_cents FROM catalog.products WHERE product_id=%s", ("demo-widget",))
                row = await cursor.fetchone()
                if row is None: raise RuntimeError("Demo product fixture missing")
                return {"productId": row[0], "name": row[1], "unitPriceCents": row[2]}


@app.get("/v1/products/demo-widget")
async def product(run_id: UUID, scenario: Scenario = "full-journey", cache_policy: CachePolicy = "default",
                  x_workshop_session_id: str | None = Header(default=None)):
    attributes = {"workshop.run_id": str(run_id), "workshop.scenario": scenario,
                  "workshop.session_id": x_workshop_session_id or os.getenv("WORKSHOP_SESSION_ID", "local-workshop"),
                  "workshop.step": "catalog.lookup"}
    with workflow_context(attributes), tracer.start_as_current_span("catalog.lookup", attributes=attributes) as span:
        try:
            bypass = cache_policy == "bypass" or scenario in ("slow-postgres", "postgres-lock-timeout", "python-error")
            key = "workshop:catalog:" + (str(run_id) if scenario == "cache-miss-hit" or cache_policy == "fresh" else "shared") + ":demo-widget"
            if not bypass:
                cached = await cache.get(key)
                if cached:
                    emit("Catalog cache hit", **attributes, **{"cache.hit": True})
                    span.set_attribute("cache.hit", True)
                    return {**json.loads(cached), "cached": True}
            emit("Catalog cache bypass" if bypass else "Catalog cache miss", **attributes, **{"cache.hit": False})
            span.set_attribute("cache.hit", False)
            if scenario == "postgres-lock-timeout":
                result = await read_product(lock_timeout=True)
            else:
                result = await read_product(slow=scenario == "slow-postgres")
            if scenario == "python-error":
                raise DemoCatalogError("DemoCatalogError: synthetic catalog lookup failure")
            if not bypass: await cache.set(key, json.dumps(result), ex=300)
            emit("Product lookup completed", **attributes)
            return {**result, "cached": False}
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            with sentry_sdk.isolation_scope() as scope:
                for key,value in attributes.items(): scope.set_tag(key,value)
                scope.set_tag("service.name", "catalog")
                event_id = sentry_sdk.capture_exception(exc)
            if isinstance(exc, psycopg.errors.LockNotAvailable):
                code = "POSTGRES_LOCK_TIMEOUT"
            elif isinstance(exc, DemoCatalogError):
                code = "DEMO_CATALOG_ERROR"
            else:
                code = "CATALOG_UNAVAILABLE"
            emit(str(exc), "ERROR", **attributes, **{"error.code": code, "sentry.event_id": event_id or ""})
            return JSONResponse(status_code=500, content={"errorCode": code, "errorEventId": event_id or "", "failureService": "catalog"})


@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "catalog"}


@app.get("/health/ready")
async def ready():
    try:
        with suppress_instrumentation():
            await cache.ping()
            async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], connect_timeout=2) as conn:
                await conn.execute("SELECT 1")
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not-ready"})
