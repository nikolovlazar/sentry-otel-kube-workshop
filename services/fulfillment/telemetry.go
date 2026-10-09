package main

import (
	"context"
	"errors"
	"os"
	"strings"
	"time"

	"github.com/getsentry/sentry-go"
	sentryotel "github.com/getsentry/sentry-go/otel"
	"github.com/jackc/pgx/v5"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	otelcodes "go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func setupTelemetry(ctx context.Context, service string) (*sdktrace.TracerProvider, error) {
	if err := sentry.Init(sentry.ClientOptions{
		Dsn: os.Getenv("SENTRY_DSN"), Environment: envDefault("WORKSHOP_ENVIRONMENT", "workshop-local"), Release: os.Getenv("SENTRY_RELEASE"),
		EnableTracing: false, SendDefaultPII: false, AttachStacktrace: true,
		BeforeSendLog: func(_ *sentry.Log) *sentry.Log { return nil },
		Integrations: func(items []sentry.Integration) []sentry.Integration {
			return append(items, sentryotel.NewOtelIntegration())
		},
	}); err != nil {
		return nil, err
	}
	exporter, err := otlptracehttp.New(ctx, otlptracehttp.WithTimeout(3*time.Second))
	if err != nil {
		return nil, err
	}
	attrs := []attribute.KeyValue{
		attribute.String("service.name", service), attribute.String("service.namespace", "sentry-otel-workshop"),
		attribute.String("service.version", envDefault("SERVICE_VERSION", os.Getenv("SENTRY_RELEASE"))), attribute.String("deployment.environment.name", envDefault("WORKSHOP_ENVIRONMENT", "workshop-local")),
	}
	for _, pair := range [][2]string{{"k8s.pod.uid", "POD_UID"}, {"k8s.pod.name", "POD_NAME"}, {"k8s.namespace.name", "POD_NAMESPACE"}, {"k8s.node.name", "NODE_NAME"}} {
		if value := os.Getenv(pair[1]); value != "" {
			attrs = append(attrs, attribute.String(pair[0], value))
		}
	}
	res, err := resource.Merge(resource.Default(), resource.NewSchemaless(attrs...))
	if err != nil {
		return nil, err
	}
	provider := sdktrace.NewTracerProvider(sdktrace.WithResource(res), sdktrace.WithSampler(sdktrace.AlwaysSample()), sdktrace.WithBatcher(exporter, sdktrace.WithBatchTimeout(500*time.Millisecond)))
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	return provider, nil
}

type metadataCarrier metadata.MD

func (m metadataCarrier) Get(key string) string {
	values := metadata.MD(m).Get(key)
	if len(values) > 0 {
		return values[0]
	}
	return ""
}
func (m metadataCarrier) Set(key, value string) { metadata.MD(m).Set(key, value) }
func (m metadataCarrier) Keys() []string {
	keys := make([]string, 0, len(m))
	for key := range m {
		keys = append(keys, key)
	}
	return keys
}

func rpcInterceptor(log *Logger) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, request any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		incoming, _ := metadata.FromIncomingContext(ctx)
		ctx = otel.GetTextMapPropagator().Extract(ctx, metadataCarrier(incoming))
		var runID, sessionID, scenario string
		if req, ok := request.(interface{ GetRunId() string }); ok {
			runID = req.GetRunId()
		}
		if req, ok := request.(interface{ GetSessionId() string }); ok {
			sessionID = req.GetSessionId()
		}
		if req, ok := request.(interface{ GetScenario() string }); ok {
			scenario = req.GetScenario()
		}
		method := info.FullMethod[strings.LastIndex(info.FullMethod, "/")+1:]
		ctx = withWorkflow(ctx, runID, sessionID, scenario, "grpc."+method)
		attributes := append(workflowAttributes(ctx, ""), attribute.String("rpc.system", "grpc"), attribute.String("rpc.method", info.FullMethod))
		ctx, span := otel.Tracer("workshop.fulfillment").Start(ctx, strings.TrimPrefix(info.FullMethod, "/"), trace.WithSpanKind(trace.SpanKindServer), trace.WithAttributes(attributes...))
		defer span.End()
		// Every RPC is bounded even if a caller omits its deadline.
		ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
		defer cancel()
		hub := sentry.CurrentHub().Clone()
		ctx = sentry.SetHubOnContext(ctx, hub)
		fields := map[string]any{"rpc.method": info.FullMethod}
		if req, ok := request.(interface{ GetRunId() string }); ok {
			fields["workshop.run_id"] = req.GetRunId()
			span.SetAttributes(attribute.String("workshop.run_id", req.GetRunId()))
			hub.Scope().SetTag("workshop.run_id", req.GetRunId())
		}
		response, err := handler(ctx, request)
		if run, ok := response.(interface {
			GetRunId() string
			GetSessionId() string
			GetScenario() string
		}); ok {
			ctx = withWorkflow(ctx, run.GetRunId(), run.GetSessionId(), run.GetScenario(), "")
			span.SetAttributes(workflowAttributes(ctx, "")...)
		}
		if err != nil {
			span.RecordError(err)
			span.SetStatus(otelcodes.Error, status.Code(err).String())
			fields["rpc.grpc.status_code"] = int(status.Code(err))
			log.Log(ctx, "ERROR", "gRPC request failed", fields)
		} else {
			log.Log(ctx, "INFO", "gRPC request completed", fields)
		}
		return response, err
	}
}

func captureException(ctx context.Context, err error, runID, scenario string) string {
	hub := sentry.GetHubFromContext(ctx)
	if hub == nil {
		hub = sentry.CurrentHub().Clone()
	}
	scope := hub.Scope().Clone()
	scope.SetTag("service.name", "fulfillment")
	for key, value := range workflowFields(ctx) {
		scope.SetTag(key, value.(string))
	}
	scope.SetTag("workshop.run_id", runID)
	scope.SetTag("workshop.scenario", scenario)
	if client := hub.Client(); client != nil {
		// EventHint.Context is required by NewOtelIntegration. Hub.CaptureException alone drops it.
		eventID := client.CaptureException(err, &sentry.EventHint{Context: ctx, OriginalException: err}, scope)
		if eventID != nil {
			return string(*eventID)
		}
	}
	return ""
}

type queryStart struct{ span trace.Span }
type queryTraceKey struct{}
type queryTracer struct{}

func (queryTracer) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	if !trace.SpanContextFromContext(ctx).IsValid() {
		return ctx
	}
	attributes := append(workflowAttributes(ctx, "postgresql.query"), attribute.String("db.system.name", "postgresql"), attribute.String("db.namespace", "workshop"), attribute.String("db.query.text", data.SQL))
	ctx, span := otel.Tracer("workshop.fulfillment.postgres").Start(ctx, "postgresql.query", trace.WithSpanKind(trace.SpanKindClient), trace.WithAttributes(attributes...))
	return context.WithValue(ctx, queryTraceKey{}, queryStart{span})
}
func (queryTracer) TraceQueryEnd(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryEndData) {
	entry, ok := ctx.Value(queryTraceKey{}).(queryStart)
	if !ok {
		return
	}
	if data.Err != nil && !errors.Is(data.Err, pgx.ErrNoRows) {
		entry.span.RecordError(data.Err)
		entry.span.SetStatus(otelcodes.Error, "query failed")
	}
	entry.span.End()
}

func databaseError(err error) error {
	if errors.Is(err, pgx.ErrNoRows) {
		return status.Error(codes.NotFound, "run not found")
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return status.Error(codes.DeadlineExceeded, "database deadline exceeded")
	}
	if errors.Is(err, context.Canceled) {
		return status.Error(codes.Canceled, "request canceled")
	}
	return status.Error(codes.Unavailable, "database operation failed")
}
