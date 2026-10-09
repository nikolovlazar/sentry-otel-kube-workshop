package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/getsentry/sentry-go"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	workshopv1 "github.com/nikolovlazar/sentry-otel-kube-workshop/services/fulfillment/gen/workshop/v1"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
)

// These tests require explicitly configured throwaway instances, never workshop data.
func TestServiceIntegration(t *testing.T) {
	databaseURL, redisURL := os.Getenv("TEST_DATABASE_URL"), os.Getenv("TEST_REDIS_URL")
	if databaseURL == "" || redisURL == "" {
		t.Skip("set TEST_DATABASE_URL and TEST_REDIS_URL to dedicated test instances")
	}
	ctx, stop := context.WithTimeout(context.Background(), 30*time.Second)
	defer stop()
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	if _, err := pool.Exec(ctx, migrations); err != nil {
		t.Fatal(err)
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(options)
	defer client.Close()
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter), sdktrace.WithSampler(sdktrace.AlwaysSample()))
	previousProvider, previousPropagator := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	defer func() {
		_ = provider.Shutdown(context.Background())
		otel.SetTracerProvider(previousProvider)
		otel.SetTextMapPropagator(previousPropagator)
	}()
	transport := &recordingTransport{}
	oldClient := sentry.CurrentHub().Client()
	sentry.CurrentHub().BindClient(testSentryClient(t, transport))
	defer sentry.CurrentHub().BindClient(oldClient)
	service := &Service{pool: pool, redis: client, log: &Logger{output: io.Discard, service: "fulfillment"}, sessionID: "test-session"}
	reset := func() {
		t.Helper()
		if _, err := pool.Exec(ctx, `TRUNCATE fulfillment.outbox,fulfillment.orders,fulfillment.runs; UPDATE fulfillment.inventory SET available_quantity=10000 WHERE product_id='demo-widget'`); err != nil {
			t.Fatal(err)
		}
		if err := client.Del(ctx, notificationStream, publishedIndex).Err(); err != nil {
			t.Fatal(err)
		}
	}
	begin := func(testContext context.Context, scenario, mode string) *workshopv1.Run {
		t.Helper()
		run, err := service.BeginRun(testContext, &workshopv1.BeginRunRequest{RunId: uuid.NewString(), SessionId: "test-session", Scenario: scenario, WorkerMode: mode, TraceId: trace.SpanContextFromContext(testContext).TraceID().String()})
		if err != nil {
			t.Fatal(err)
		}
		return run
	}
	submit := func(testContext context.Context, run *workshopv1.Run) (*workshopv1.Run, error) {
		return service.SubmitOrder(testContext, &workshopv1.SubmitOrderRequest{RunId: run.RunId, ProductId: "demo-widget", Quantity: 1, UnitPriceCents: 2999})
	}

	t.Run("order and outbox are idempotent and producer preserves trace", func(t *testing.T) {
		reset()
		workflow, span := provider.Tracer("test").Start(ctx, "test workflow")
		defer span.End()
		run := begin(workflow, "full-journey", "success")
		if _, err := submit(workflow, run); err != nil {
			t.Fatal(err)
		}
		if _, err := submit(workflow, run); err != nil {
			t.Fatal(err)
		}
		var stock, orders, outbox int
		if err := pool.QueryRow(ctx, `SELECT available_quantity,(SELECT count(*) FROM fulfillment.orders),(SELECT count(*) FROM fulfillment.outbox) FROM fulfillment.inventory WHERE product_id='demo-widget'`).Scan(&stock, &orders, &outbox); err != nil {
			t.Fatal(err)
		}
		if stock != 9999 || orders != 1 || outbox != 1 {
			t.Fatalf("duplicate effects: stock=%d orders=%d outbox=%d", stock, orders, outbox)
		}
		if published, err := service.publishOne(ctx); err != nil || !published {
			t.Fatalf("publish=%v error=%v", published, err)
		}
		messages, err := client.XRange(ctx, notificationStream, "-", "+").Result()
		if err != nil || len(messages) != 1 {
			t.Fatalf("messages=%d error=%v", len(messages), err)
		}
		message := messages[0]
		carrier := propagation.MapCarrier{}
		for key, value := range message.Values {
			if text, ok := value.(string); ok {
				carrier[key] = text
			}
		}
		consumed := otel.GetTextMapPropagator().Extract(context.Background(), carrier)
		for key, expected := range map[string]string{"workshop.run_id": run.RunId, "workshop.session_id": run.SessionId, "workshop.scenario": run.Scenario, "workshop.step": "notification.publish"} {
			if actual := workflowFields(consumed)[key]; actual != expected {
				t.Fatalf("published carrier lost %s: %v", key, actual)
			}
		}
		consumerParent := trace.SpanContextFromContext(consumed)
		if consumerParent.TraceID().String() != run.TraceId {
			t.Fatalf("outbox split trace: %s != %s", consumerParent.TraceID(), run.TraceId)
		}
		var raw []byte
		if err := pool.QueryRow(ctx, `SELECT carrier FROM fulfillment.outbox WHERE run_id=$1`, run.RunId).Scan(&raw); err != nil {
			t.Fatal(err)
		}
		original := propagation.MapCarrier{}
		if err := json.Unmarshal(raw, &original); err != nil {
			t.Fatal(err)
		}
		parent := trace.SpanContextFromContext(otel.GetTextMapPropagator().Extract(context.Background(), original))
		matched := false
		for _, sent := range exporter.GetSpans() {
			if sent.Name == "notifications publish" && sent.SpanContext.SpanID() == consumerParent.SpanID() {
				matched = sent.SpanKind == trace.SpanKindProducer && sent.Parent.SpanID() == parent.SpanID()
			}
		}
		if !matched {
			t.Fatal("message does not point to a PRODUCER child of the persisted upstream span")
		}
		// Simulate a publisher crash after Redis succeeded but before outbox acknowledgment.
		if _, err := pool.Exec(ctx, `UPDATE fulfillment.outbox SET published_at=NULL WHERE run_id=$1`, run.RunId); err != nil {
			t.Fatal(err)
		}
		if _, err := service.publishOne(ctx); err != nil {
			t.Fatal(err)
		}
		after, err := client.XRange(ctx, notificationStream, "-", "+").Result()
		if err != nil || len(after) != 1 || after[0].ID != message.ID {
			t.Fatal("republishing created a second Stream message")
		}
		for _, attempt := range []int32{1, 2} {
			result, err := service.RecordNotificationResult(workflow, &workshopv1.RecordNotificationResultRequest{RunId: run.RunId, MessageId: message.ID, Attempt: attempt, Outcome: "retry", ErrorCode: "DEMO_NOTIFICATION_ERROR"})
			if err != nil || result.Status != "retrying" || result.Attempts != attempt {
				t.Fatalf("retry result=%v error=%v", result, err)
			}
		}
		completed, err := service.RecordNotificationResult(workflow, &workshopv1.RecordNotificationResultRequest{RunId: run.RunId, MessageId: message.ID, Attempt: 3, Outcome: "succeeded"})
		if err != nil || completed.Status != "completed" || completed.Attempts != 3 {
			t.Fatalf("completion=%v error=%v", completed, err)
		}
		duplicate, err := service.RecordNotificationResult(workflow, &workshopv1.RecordNotificationResultRequest{RunId: run.RunId, MessageId: message.ID, Attempt: 1, Outcome: "retry", ErrorCode: "DEMO_NOTIFICATION_ERROR"})
		if err != nil || duplicate.Status != "completed" || duplicate.Attempts != 3 {
			t.Fatalf("terminal result changed: %v %v", duplicate, err)
		}
	})

	t.Run("injected gRPC failure rolls back inventory and captures once", func(t *testing.T) {
		reset()
		workflow, span := provider.Tracer("test").Start(ctx, "Go error workflow")
		defer span.End()
		run := begin(workflow, "go-error", "success")
		listener := bufconn.Listen(1024 * 1024)
		server := grpc.NewServer(grpc.UnaryInterceptor(rpcInterceptor(service.log)))
		workshopv1.RegisterFulfillmentServer(server, service)
		go func() { _ = server.Serve(listener) }()
		defer server.Stop()
		conn, err := grpc.NewClient("passthrough:///test", grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }))
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		carrier := propagation.MapCarrier{}
		otel.GetTextMapPropagator().Inject(workflow, carrier)
		outgoing := metadata.NewOutgoingContext(workflow, metadata.New(map[string]string(carrier)))
		var trailers metadata.MD
		rpc := workshopv1.NewFulfillmentClient(conn)
		request := &workshopv1.SubmitOrderRequest{RunId: run.RunId, ProductId: "demo-widget", Quantity: 1, UnitPriceCents: 2999}
		if _, err := rpc.SubmitOrder(outgoing, request, grpc.Trailer(&trailers)); status.Code(err) != codes.Internal {
			t.Fatalf("got %v", err)
		}
		failed, err := service.GetRun(workflow, &workshopv1.GetRunRequest{RunId: run.RunId})
		if err != nil || failed.Status != "failed" || failed.ErrorEventId == "" {
			t.Fatalf("failure=%v error=%v", failed, err)
		}
		if got := trailers.Get("sentry-event-id"); len(got) != 1 || got[0] != failed.ErrorEventId {
			t.Fatalf("missing Sentry error trailer: %#v", trailers)
		}
		if got := trailers.Get("workshop-error-code"); len(got) != 1 || got[0] != "DEMO_RESERVATION_ERROR" {
			t.Fatal("missing stable error code trailer")
		}
		var stock, orders, outbox int
		if err := pool.QueryRow(ctx, `SELECT available_quantity,(SELECT count(*) FROM fulfillment.orders),(SELECT count(*) FROM fulfillment.outbox) FROM fulfillment.inventory WHERE product_id='demo-widget'`).Scan(&stock, &orders, &outbox); err != nil {
			t.Fatal(err)
		}
		if stock != 10000 || orders != 0 || outbox != 0 {
			t.Fatalf("rollback failed: stock=%d orders=%d outbox=%d", stock, orders, outbox)
		}
		before := len(transport.Events())
		if _, err := rpc.SubmitOrder(outgoing, request); status.Code(err) != codes.FailedPrecondition {
			t.Fatalf("second submission: %v", err)
		}
		if len(transport.Events()) != before {
			t.Fatal("duplicate submission captured another SDK event")
		}
		if transport.Events()[before-1].Contexts["trace"]["trace_id"] != run.TraceId {
			t.Fatal("Go SDK error lost workflow trace")
		}
	})

	t.Run("origin failures and dead letters preserve terminal truth", func(t *testing.T) {
		reset()
		workflow, span := provider.Tracer("test").Start(ctx, "dead letter workflow")
		defer span.End()
		run := begin(workflow, "async-worker", "exhaust")
		if _, err := submit(workflow, run); err != nil {
			t.Fatal(err)
		}
		result, err := service.RecordNotificationResult(workflow, &workshopv1.RecordNotificationResultRequest{RunId: run.RunId, MessageId: "123-0", Attempt: 3, Outcome: "dead_lettered", ErrorCode: "DEMO_NOTIFICATION_ERROR", ErrorEventId: "5e785c0f256354d5316771fe27557be2"})
		if err != nil || result.Status != "dead_lettered" || result.Attempts != 3 {
			t.Fatalf("result=%v error=%v", result, err)
		}
		again, err := service.RecordFailure(workflow, &workshopv1.RecordFailureRequest{RunId: run.RunId, FailureService: "web", ErrorCode: "UPSTREAM_FAILURE"})
		if err != nil || again.Status != "dead_lettered" || again.FailureService != "notification-worker" {
			t.Fatal("upstream failure overwrote the originating dead letter")
		}
		conflict := &workshopv1.BeginRunRequest{RunId: run.RunId, SessionId: "test-session", Scenario: "full-journey", WorkerMode: "success", TraceId: run.TraceId}
		if _, err := service.BeginRun(workflow, conflict); status.Code(err) != codes.AlreadyExists {
			t.Fatal("run specification was mutable")
		}
		if _, err := service.ListRuns(workflow, &workshopv1.ListRunsRequest{SessionId: "different-session"}); status.Code(err) != codes.InvalidArgument {
			t.Fatal("session boundary was ignored")
		}
	})
	t.Run("migration works for restricted schema owner", func(t *testing.T) {
		suffix := strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
		role, schema := "fulfillment_test_"+suffix, "fulfillment_test_schema_"+suffix
		if _, err := pool.Exec(ctx, `CREATE ROLE `+role+` LOGIN PASSWORD 'workshop_role_test'; CREATE SCHEMA `+schema+` AUTHORIZATION `+role); err != nil {
			t.Fatal(err)
		}
		defer func() { _, _ = pool.Exec(context.Background(), `DROP SCHEMA `+schema+` CASCADE; DROP ROLE `+role) }()
		config, err := pgxpool.ParseConfig(databaseURL)
		if err != nil {
			t.Fatal(err)
		}
		config.ConnConfig.User, config.ConnConfig.Password = role, "workshop_role_test"
		owner, err := pgxpool.NewWithConfig(ctx, config)
		if err != nil {
			t.Fatal(err)
		}
		defer owner.Close()
		scopedMigration := strings.ReplaceAll(migrations, "fulfillment", schema)
		if _, err := owner.Exec(ctx, scopedMigration); err != nil {
			t.Fatalf("restricted schema owner cannot apply migration: %v", err)
		}
		if _, err := owner.Exec(ctx, scopedMigration); err != nil {
			t.Fatalf("migration is not repeatable: %v", err)
		}
	})

}
