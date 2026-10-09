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
    RedisInstrumentor().instrument()
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
