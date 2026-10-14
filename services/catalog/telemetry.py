"""OTel owns tracing; Sentry links errors, and stdout is the sole log exporter."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager

import sentry_sdk
from sentry_sdk.integrations.otlp import OTLPIntegration
from opentelemetry import trace, baggage, context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor


WORKFLOW_KEYS = ("workshop.run_id", "workshop.session_id", "workshop.scenario")


class WorkflowAttributes(SpanProcessor):
    def on_start(self, span, parent_context=None):
        fields = {key: value for key in WORKFLOW_KEYS if (value := baggage.get_baggage(key, parent_context)) is not None}
        if fields:
            fields["workshop.step"] = span.name
            span.set_attributes(fields)

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


@contextmanager
def workflow_context(attributes):
    carrier_context = context.get_current()
    for key in WORKFLOW_KEYS:
        carrier_context = baggage.set_baggage(key, attributes[key], carrier_context)
    token = context.attach(carrier_context)
    try:
        yield
    finally:
        context.detach(token)


def postgres_application_name():
    """Carry only valid active business-span IDs in PostgreSQL's 63-byte field."""
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        return f"w:{span_context.trace_id:032x}:{span_context.span_id:016x}"
    return "workshop-catalog"


def redis_request_hook(span, instance, args, kwargs):
    """Describe synthetic cache commands without exporting cached payloads."""
    if not span or not span.is_recording() or not args:
        return
    command = str(args[0]).upper()
    if command not in {"GET", "SET"} or len(args) < 2:
        return
    key = str(args[1])
    if not key.startswith("workshop:catalog:"):
        return
    # Run IDs belong in workshop.run_id; keep query grouping deterministic.
    description = command + " workshop:catalog:<scope>:demo-widget"
    span.update_name("workshop:catalog:<scope>:demo-widget")
    span.set_attributes({"db.system": "redis", "db.system.name": "redis",
                         "db.operation.name": command, "db.statement": description,
                         "db.query.text": description,
                         "sentry.op": "cache.get" if command == "GET" else "cache.put",
                         "cache.operation": "get" if command == "GET" else "put",
                         "cache.key": ["workshop:catalog:<scope>:demo-widget"]})
    for target, modern, legacy in [("network.peer.address", "server.address", "net.peer.name"),
                                   ("network.peer.port", "server.port", "net.peer.port")]:
        value = span.attributes.get(modern, span.attributes.get(legacy))
        if value is not None:
            span.set_attribute(target, value)
    if command == "SET":
        value = args[2]
        span.set_attribute("cache.item_size", len(value.encode("utf-8") if isinstance(value, str) else value))
        # redis-py serializes set(..., ex=seconds) into positional EX arguments.
        for index in range(3, len(args) - 1):
            if str(args[index]).upper() == "EX":
                span.set_attribute("cache.ttl", int(args[index + 1]))
                break


def redis_response_hook(span, instance, response):
    """Record the outcome on the same OTel cache span that performed the IO."""
    if not span or not span.is_recording():
        return
    operation = span.attributes.get("cache.operation")
    if operation == "get":
        hit = response is not None
        fields = {"cache.hit": hit}
        if hit:
            fields["cache.item_size"] = len(response.encode("utf-8") if isinstance(response, str) else response)
        span.set_attributes(fields)
        emit("Catalog cache hit" if hit else "Catalog cache miss", **fields)
    elif operation == "put":
        span.set_attribute("cache.write", bool(response))


def configure():
    attrs = {
        "service.name": os.getenv("OTEL_SERVICE_NAME", "catalog"),
        "service.namespace": "sentry-otel-workshop",
        "service.version": os.getenv("SERVICE_VERSION", "local"),
        "deployment.environment.name": os.getenv("WORKSHOP_ENVIRONMENT", "workshop-local"),
    }
    for env, key in [("POD_UID", "k8s.pod.uid"), ("POD_NAME", "k8s.pod.name"),
                     ("POD_NAMESPACE", "k8s.namespace.name"), ("NODE_NAME", "k8s.node.name")]:
        if os.getenv(env): attrs[key] = os.environ[env]
    provider = TracerProvider(resource=Resource.create(attrs), sampler=ALWAYS_ON)
    base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318").rstrip("/")
    provider.add_span_processor(WorkflowAttributes())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=base + "/v1/traces")))
    trace.set_tracer_provider(provider)
    set_global_textmap(CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()]))
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN") or None,
        release=os.getenv("SENTRY_RELEASE", "sentry-otel-kube-workshop@local"),
        environment=os.getenv("WORKSHOP_ENVIRONMENT", "workshop-local"),
        send_default_pii=False,
        integrations=[OTLPIntegration(setup_otlp_traces_exporter=False, setup_propagator=False)],
    )
    PsycopgInstrumentor().instrument()
    RedisInstrumentor().instrument(request_hook=redis_request_hook, response_hook=redis_response_hook)
    return provider


def emit(body, severity="INFO", **attributes):
    context = trace.get_current_span().get_span_context()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": severity, "body": body,
        "service.name": os.getenv("OTEL_SERVICE_NAME", "catalog"),
        "log.record_id": str(uuid.uuid4()),
        **{key: value for key in WORKFLOW_KEYS if (value := baggage.get_baggage(key)) is not None},
        "workshop.step": getattr(trace.get_current_span(), "name", "application.log"),
        **attributes,
    }
    if context.is_valid:
        record.update(trace_id=format(context.trace_id, "032x"), span_id=format(context.span_id, "016x"),
                      trace_flags=format(int(context.trace_flags), "02x"))
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()
