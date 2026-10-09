package main

import (
	"bytes"
	"context"
	"encoding/json"
	"sync"
	"testing"
	"time"

	"github.com/getsentry/sentry-go"
	sentryotel "github.com/getsentry/sentry-go/otel"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	workshopv1 "github.com/nikolovlazar/sentry-otel-kube-workshop/services/fulfillment/gen/workshop/v1"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type recordingTransport struct {
	mu     sync.Mutex
	events []*sentry.Event
}

func (*recordingTransport) Configure(sentry.ClientOptions)        {}
func (*recordingTransport) Flush(time.Duration) bool              { return true }
func (*recordingTransport) FlushWithContext(context.Context) bool { return true }
func (*recordingTransport) Close()                                {}
func (r *recordingTransport) SendEvent(event *sentry.Event) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = append(r.events, event)
}
func (r *recordingTransport) Events() []*sentry.Event {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]*sentry.Event{}, r.events...)
}
func testSentryClient(t *testing.T, transport *recordingTransport) *sentry.Client {
	t.Helper()
	client, err := sentry.NewClient(sentry.ClientOptions{Dsn: "https://public@example.invalid/1", Transport: transport, AttachStacktrace: true, Integrations: func(items []sentry.Integration) []sentry.Integration {
		return append(items, sentryotel.NewOtelIntegration())
	}})
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func TestErrorCaptureLinksToActiveOTelContext(t *testing.T) {
	traceID, _ := trace.TraceIDFromHex("5e785c0f256354d5316771fe27557be2")
	spanID, _ := trace.SpanIDFromHex("14f7caa5c7bb8086")
	ctx := trace.ContextWithSpanContext(context.Background(), trace.NewSpanContext(trace.SpanContextConfig{TraceID: traceID, SpanID: spanID, TraceFlags: trace.FlagsSampled}))
	transport := &recordingTransport{}
	ctx = sentry.SetHubOnContext(ctx, sentry.NewHub(testSentryClient(t, transport), sentry.NewScope()))
	eventID := captureException(ctx, &DemoReservationError{}, uuid.NewString(), "go-error")
	events := transport.Events()
	if eventID == "" || len(events) != 1 {
		t.Fatalf("expected one SDK event, got id=%q count=%d", eventID, len(events))
	}
	linked := events[0].Contexts["trace"]
	if linked["trace_id"] != traceID.String() || linked["span_id"] != spanID.String() {
		t.Fatalf("SDK event lost active OTel context: %#v", linked)
	}
}

func TestJSONLogRetainsNativeTraceIdentifiers(t *testing.T) {
	var output bytes.Buffer
	logger := &Logger{output: &output, service: "fulfillment"}
	traceID, _ := trace.TraceIDFromHex("5e785c0f256354d5316771fe27557be2")
	spanID, _ := trace.SpanIDFromHex("14f7caa5c7bb8086")
	ctx := trace.ContextWithSpanContext(context.Background(), trace.NewSpanContext(trace.SpanContextConfig{TraceID: traceID, SpanID: spanID, TraceFlags: trace.FlagsSampled}))
	logger.Log(ctx, "ERROR", "reservation failed", map[string]any{"workshop.run_id": "example"})
	var record map[string]any
	if err := json.Unmarshal(output.Bytes(), &record); err != nil {
		t.Fatal(err)
	}
	if record["trace_id"] != traceID.String() || record["span_id"] != spanID.String() || record["trace_flags"] != float64(1) {
		t.Fatalf("invalid correlation fields: %#v", record)
	}
	if record["body"] != "reservation failed" || record["severity"] != "ERROR" || record["service.name"] != "fulfillment" {
		t.Fatalf("invalid log contract: %#v", record)
	}
	if _, err := uuid.Parse(record["log.record_id"].(string)); err != nil {
		t.Fatal("missing unique log record ID")
	}
	if _, err := time.Parse(time.RFC3339Nano, record["timestamp"].(string)); err != nil {
		t.Fatal("invalid timestamp")
	}
}

func TestNotificationAttemptBoundaries(t *testing.T) {
	for _, test := range []struct {
		name, outcome string
		attempt       int32
		want          codes.Code
	}{
		{"early retry", "retry", 1, codes.OK}, {"third retry", "retry", 3, codes.InvalidArgument},
		{"early dead letter", "dead_lettered", 2, codes.InvalidArgument}, {"exhausted", "dead_lettered", 3, codes.OK},
		{"success", "succeeded", 3, codes.OK}, {"zero attempt", "succeeded", 0, codes.InvalidArgument},
		{"over budget", "succeeded", 4, codes.InvalidArgument}, {"unknown outcome", "completed", 1, codes.InvalidArgument},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := &workshopv1.RecordNotificationResultRequest{RunId: uuid.NewString(), MessageId: "123456-0", Attempt: test.attempt, Outcome: test.outcome, ErrorCode: "DEMO_NOTIFICATION_ERROR"}
			if actual := status.Code(validateNotification(request)); actual != test.want {
				t.Fatalf("got %s, want %s", actual, test.want)
			}
		})
	}
}

// The real interceptor, query tracer and stdout path must retain a workflow received via W3C baggage.
func TestRPCWorkflowMetadataReachesDatabaseSpansAndLogs(t *testing.T) {
	t.Setenv("WORKSHOP_SESSION_ID", "default-session")
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	previousProvider, previousPropagator := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	defer func() {
		_ = provider.Shutdown(context.Background())
		otel.SetTracerProvider(previousProvider)
		otel.SetTextMapPropagator(previousPropagator)
	}()
	var output bytes.Buffer
	logger := &Logger{output: &output, service: "fulfillment"}
	runID := uuid.NewString()
	ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs(
		"traceparent", "00-5e785c0f256354d5316771fe27557be2-14f7caa5c7bb8086-01",
		"baggage", "workshop.run_id="+runID+",workshop.session_id=carried-session,workshop.scenario=full-journey",
	))
	_, err := rpcInterceptor(logger)(ctx, &workshopv1.SubmitOrderRequest{RunId: runID},
		&grpc.UnaryServerInfo{FullMethod: "/workshop.v1.Fulfillment/SubmitOrder"},
		func(ctx context.Context, _ any) (any, error) {
			queryContext := (queryTracer{}).TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{SQL: "SELECT 1"})
			(queryTracer{}).TraceQueryEnd(queryContext, nil, pgx.TraceQueryEndData{})
			return &workshopv1.Run{RunId: runID, SessionId: "carried-session", Scenario: "full-journey"}, nil
		})
	if err != nil {
		t.Fatal(err)
	}
	var record map[string]any
	if err := json.Unmarshal(output.Bytes(), &record); err != nil {
		t.Fatal(err)
	}
	for key, expected := range map[string]string{
		"workshop.run_id": runID, "workshop.session_id": "carried-session", "workshop.scenario": "full-journey", "workshop.step": "grpc.SubmitOrder",
	} {
		if record[key] != expected {
			t.Fatalf("RPC completion lost %s: %#v", key, record)
		}
	}
	spans := exporter.GetSpans()
	if len(spans) != 2 {
		t.Fatalf("expected query and RPC spans, got %d", len(spans))
	}
	for _, span := range spans {
		attributes := map[string]string{}
		for _, value := range span.Attributes {
			if value.Value.Type() == attribute.STRING {
				attributes[string(value.Key)] = value.Value.AsString()
			}
		}
		for key, expected := range map[string]string{"workshop.run_id": runID, "workshop.session_id": "carried-session", "workshop.scenario": "full-journey"} {
			if attributes[key] != expected {
				t.Fatalf("span %s lost %s: %#v", span.Name, key, attributes)
			}
		}
		expectedStep := "grpc.SubmitOrder"
		if span.Name == "postgresql.query" {
			expectedStep = "postgresql.query"
		}
		if attributes["workshop.step"] != expectedStep {
			t.Fatalf("span %s has wrong step: %#v", span.Name, attributes)
		}
		if span.SpanContext.TraceID().String() != "5e785c0f256354d5316771fe27557be2" {
			t.Fatal("workflow metadata must not split trace context")
		}
		if span.Name == "workshop.v1.Fulfillment/SubmitOrder" && span.Parent.SpanID().String() != "14f7caa5c7bb8086" {
			t.Fatal("RPC lost upstream parent")
		}
	}
}
