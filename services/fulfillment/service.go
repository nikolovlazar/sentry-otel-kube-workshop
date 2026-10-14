package main

import (
	"context"
	"encoding/hex"
	"errors"
	"regexp"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	workshopv1 "github.com/nikolovlazar/sentry-otel-kube-workshop/services/fulfillment/gen/workshop/v1"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	otelcodes "go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type Service struct {
	workshopv1.UnimplementedFulfillmentServer
	pool      *pgxpool.Pool
	redis     *redis.Client
	log       *Logger
	sessionID string
}

var scenarios = map[string]bool{
	"full-journey": true, "cache-miss-hit": true, "slow-postgres": true, "postgres-lock-timeout": true, "python-error": true,
	"go-error": true, "async-worker": true, "next-error": true, "browser-error": true,
}
var safeName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,80}$`)
var messageIDPattern = regexp.MustCompile(`^[0-9]+-[0-9]+$`)

const runColumns = `run_id::text,session_id,scenario,worker_mode,status,trace_id,attempts,failure_service,error_code,error_event_id,created_at,updated_at`

func scanRun(row pgx.Row) (*workshopv1.Run, error) {
	run := &workshopv1.Run{}
	var created, updated time.Time
	err := row.Scan(&run.RunId, &run.SessionId, &run.Scenario, &run.WorkerMode, &run.Status, &run.TraceId, &run.Attempts, &run.FailureService, &run.ErrorCode, &run.ErrorEventId, &created, &updated)
	if err != nil {
		return nil, err
	}
	run.CreatedAt, run.UpdatedAt = created.UTC().Format(time.RFC3339Nano), updated.UTC().Format(time.RFC3339Nano)
	return run, nil
}

func validRunID(value string) bool {
	id, err := uuid.Parse(value)
	return err == nil && id.String() == value
}
func validHexID(value string) bool {
	if len(value) != 32 {
		return false
	}
	decoded, err := hex.DecodeString(value)
	if err != nil {
		return false
	}
	for _, b := range decoded {
		if b != 0 {
			return true
		}
	}
	return false
}
func terminal(state string) bool {
	return state == "completed" || state == "failed" || state == "dead_lettered"
}
func runFields(run *workshopv1.Run) map[string]any {
	return map[string]any{"workshop.run_id": run.RunId, "workshop.session_id": run.SessionId, "workshop.scenario": run.Scenario, "workshop.worker_mode": run.WorkerMode, "workshop.status": run.Status, "workshop.attempt": run.Attempts}
}
func annotate(ctx context.Context, run *workshopv1.Run) context.Context {
	ctx = withWorkflow(ctx, run.RunId, run.SessionId, run.Scenario, "")
	attributes := append(workflowAttributes(ctx, ""), attribute.String("workshop.worker_mode", run.WorkerMode))
	trace.SpanFromContext(ctx).SetAttributes(attributes...)
	return ctx
}

func (s *Service) BeginRun(ctx context.Context, request *workshopv1.BeginRunRequest) (*workshopv1.Run, error) {
	if !validRunID(request.RunId) || !safeName.MatchString(request.SessionId) || request.SessionId != s.sessionID || !scenarios[request.Scenario] || !validHexID(request.TraceId) {
		return nil, status.Error(codes.InvalidArgument, "invalid run specification")
	}
	mode := request.WorkerMode
	if mode == "" {
		mode = "success"
	}
	if mode != "success" && mode != "recover" && mode != "exhaust" {
		return nil, status.Error(codes.InvalidArgument, "invalid worker mode")
	}
	if request.Scenario != "async-worker" && mode != "success" {
		return nil, status.Error(codes.InvalidArgument, "worker faults require async-worker scenario")
	}
	_, err := s.pool.Exec(ctx, `INSERT INTO fulfillment.runs(run_id,session_id,scenario,worker_mode,trace_id) VALUES($1,$2,$3,$4,$5) ON CONFLICT(run_id) DO NOTHING`, request.RunId, request.SessionId, request.Scenario, mode, request.TraceId)
	if err != nil {
		return nil, databaseError(err)
	}
	run, err := s.getRun(ctx, request.RunId)
	if err != nil {
		return nil, databaseError(err)
	}
	if run.SessionId != request.SessionId || run.Scenario != request.Scenario || run.WorkerMode != mode || run.TraceId != request.TraceId {
		return nil, status.Error(codes.AlreadyExists, "run ID already has a different specification")
	}
	ctx = annotate(ctx, run)
	s.log.Log(ctx, "INFO", "Demo run started", runFields(run))
	return run, nil
}

func (s *Service) getRun(ctx context.Context, runID string) (*workshopv1.Run, error) {
	return scanRun(s.pool.QueryRow(ctx, `SELECT `+runColumns+` FROM fulfillment.runs WHERE run_id=$1 AND session_id=$2`, runID, s.sessionID))
}
func (s *Service) GetRun(ctx context.Context, request *workshopv1.GetRunRequest) (*workshopv1.Run, error) {
	if !validRunID(request.RunId) {
		return nil, status.Error(codes.InvalidArgument, "invalid run ID")
	}
	run, err := s.getRun(ctx, request.RunId)
	if err != nil {
		return nil, databaseError(err)
	}
	ctx = annotate(ctx, run)
	return run, nil
}
func (s *Service) ListRuns(ctx context.Context, request *workshopv1.ListRunsRequest) (*workshopv1.ListRunsResponse, error) {
	if request.SessionId != s.sessionID {
		return nil, status.Error(codes.InvalidArgument, "invalid workshop session")
	}
	rows, err := s.pool.Query(ctx, `SELECT `+runColumns+` FROM fulfillment.runs WHERE session_id=$1 ORDER BY created_at DESC LIMIT 20`, request.SessionId)
	if err != nil {
		return nil, databaseError(err)
	}
	defer rows.Close()
	response := &workshopv1.ListRunsResponse{}
	for rows.Next() {
		run, err := scanRun(rows)
		if err != nil {
			return nil, databaseError(err)
		}
		response.Runs = append(response.Runs, run)
	}
	if rows.Err() != nil {
		return nil, databaseError(rows.Err())
	}
	return response, nil
}

type DemoReservationError struct{}

func (*DemoReservationError) Error() string {
	return "Intentional inventory reservation failure after UPDATE; the transaction is rolled back"
}

func (s *Service) SubmitOrder(ctx context.Context, request *workshopv1.SubmitOrderRequest) (*workshopv1.Run, error) {
	if !validRunID(request.RunId) || request.ProductId != "demo-widget" || request.Quantity != 1 || request.UnitPriceCents <= 0 || request.UnitPriceCents > 1000000 {
		return nil, status.Error(codes.InvalidArgument, "invalid demo order")
	}
	ctx = withWorkflow(ctx, request.RunId, s.sessionID, "", "fulfillment.submit_order")
	ctx, span := otel.Tracer("workshop.fulfillment").Start(ctx, "fulfillment.submit_order", trace.WithAttributes(workflowAttributes(ctx, "")...))
	defer span.End()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, databaseError(err)
	}
	defer rollback(ctx, tx)
	run, err := scanRun(tx.QueryRow(ctx, `SELECT `+runColumns+` FROM fulfillment.runs WHERE run_id=$1 AND session_id=$2 FOR UPDATE`, request.RunId, s.sessionID))
	if err != nil {
		return nil, databaseError(err)
	}
	ctx = annotate(ctx, run)
	if run.Status != "running" {
		if run.Status == "failed" {
			return nil, status.Error(codes.FailedPrecondition, "failed run cannot submit an order")
		}
		s.log.Log(ctx, "INFO", "Duplicate order submission has no additional effect", runFields(run))
		return run, nil
	}
	if _, err := tx.Exec(ctx, `SAVEPOINT reserve_inventory`); err != nil {
		return nil, databaseError(err)
	}
	var remaining int
	err = tx.QueryRow(ctx, `UPDATE fulfillment.inventory SET available_quantity=available_quantity-1 WHERE product_id=$1 AND available_quantity>0 RETURNING available_quantity`, request.ProductId).Scan(&remaining)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, status.Error(codes.ResourceExhausted, "demo inventory is exhausted; reset the demo")
	}
	if err != nil {
		return nil, databaseError(err)
	}
	s.log.Log(ctx, "INFO", "Inventory reserved inside order transaction", runFields(run))
	if run.Scenario == "go-error" {
		injected := &DemoReservationError{}
		span.RecordError(injected)
		span.SetStatus(otelcodes.Error, "intentional reservation failure")
		if _, err := tx.Exec(ctx, `ROLLBACK TO SAVEPOINT reserve_inventory`); err != nil {
			return nil, databaseError(err)
		}
		eventID := captureException(ctx, injected, run.RunId, run.Scenario)
		fields := runFields(run)
		fields["workshop.error_code"], fields["sentry.event_id"] = "DEMO_RESERVATION_ERROR", eventID
		s.log.Log(ctx, "ERROR", injected.Error(), fields)
		run, err = scanRun(tx.QueryRow(ctx, `UPDATE fulfillment.runs SET status='failed',failure_service='fulfillment',error_code='DEMO_RESERVATION_ERROR',error_event_id=$3,updated_at=now() WHERE run_id=$1 AND session_id=$2 RETURNING `+runColumns, run.RunId, s.sessionID, eventID))
		if err != nil {
			return nil, databaseError(err)
		}
		if err := tx.Commit(ctx); err != nil {
			return nil, databaseError(err)
		}
		_ = grpc.SetTrailer(ctx, metadata.Pairs("sentry-event-id", eventID, "workshop-error-code", "DEMO_RESERVATION_ERROR"))
		return nil, status.Error(codes.Internal, injected.Error())
	}
	if _, err := tx.Exec(ctx, `INSERT INTO fulfillment.orders(run_id,product_id,quantity,unit_price_cents) VALUES($1,$2,$3,$4)`, request.RunId, request.ProductId, request.Quantity, request.UnitPriceCents); err != nil {
		return nil, databaseError(err)
	}
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	if _, err := tx.Exec(ctx, `INSERT INTO fulfillment.outbox(run_id,carrier) VALUES($1,$2)`, request.RunId, map[string]string(carrier)); err != nil {
		return nil, databaseError(err)
	}
	run, err = scanRun(tx.QueryRow(ctx, `UPDATE fulfillment.runs SET status='queued',updated_at=now() WHERE run_id=$1 AND session_id=$2 RETURNING `+runColumns, request.RunId, s.sessionID))
	if err != nil {
		return nil, databaseError(err)
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, databaseError(err)
	}
	s.log.Log(ctx, "INFO", "Order committed with durable notification outbox", runFields(run))
	return run, nil
}

func (s *Service) RecordFailure(ctx context.Context, request *workshopv1.RecordFailureRequest) (*workshopv1.Run, error) {
	origins := map[string]bool{"web": true, "catalog": true, "fulfillment": true, "notification-worker": true, "browser": true}
	if !validRunID(request.RunId) || !origins[request.FailureService] || !safeName.MatchString(request.ErrorCode) || (request.ErrorEventId != "" && !validHexID(request.ErrorEventId)) {
		return nil, status.Error(codes.InvalidArgument, "invalid failure record")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, databaseError(err)
	}
	defer rollback(ctx, tx)
	run, err := scanRun(tx.QueryRow(ctx, `SELECT `+runColumns+` FROM fulfillment.runs WHERE run_id=$1 AND session_id=$2 FOR UPDATE`, request.RunId, s.sessionID))
	if err != nil {
		return nil, databaseError(err)
	}
	ctx = annotate(ctx, run)
	if terminal(run.Status) {
		return run, nil
	}
	run, err = scanRun(tx.QueryRow(ctx, `UPDATE fulfillment.runs SET status='failed',failure_service=$3,error_code=$4,error_event_id=$5,updated_at=now() WHERE run_id=$1 AND session_id=$2 RETURNING `+runColumns, request.RunId, s.sessionID, request.FailureService, request.ErrorCode, request.ErrorEventId))
	if err != nil {
		return nil, databaseError(err)
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, databaseError(err)
	}
	fields := runFields(run)
	fields["workshop.failure_service"], fields["workshop.error_code"], fields["sentry.event_id"] = run.FailureService, run.ErrorCode, run.ErrorEventId
	s.log.Log(ctx, "WARN", "Origin service failure recorded without duplicate SDK capture", fields)
	return run, nil
}

func validateNotification(request *workshopv1.RecordNotificationResultRequest) error {
	if !validRunID(request.RunId) || !messageIDPattern.MatchString(request.MessageId) || request.Attempt < 1 || request.Attempt > 3 || (request.ErrorEventId != "" && !validHexID(request.ErrorEventId)) {
		return status.Error(codes.InvalidArgument, "invalid notification result")
	}
	switch request.Outcome {
	case "retry":
		if request.Attempt == 3 {
			return status.Error(codes.InvalidArgument, "attempt three must be terminal")
		}
	case "succeeded":
	case "dead_lettered":
		if request.Attempt != 3 {
			return status.Error(codes.InvalidArgument, "dead letters require three attempts")
		}
	default:
		return status.Error(codes.InvalidArgument, "unknown notification outcome")
	}
	if request.Outcome != "succeeded" && !safeName.MatchString(request.ErrorCode) {
		return status.Error(codes.InvalidArgument, "notification failure requires an error code")
	}
	return nil
}
func (s *Service) RecordNotificationResult(ctx context.Context, request *workshopv1.RecordNotificationResultRequest) (*workshopv1.Run, error) {
	if err := validateNotification(request); err != nil {
		return nil, err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, databaseError(err)
	}
	defer rollback(ctx, tx)
	run, err := scanRun(tx.QueryRow(ctx, `SELECT `+runColumns+` FROM fulfillment.runs WHERE run_id=$1 AND session_id=$2 FOR UPDATE`, request.RunId, s.sessionID))
	if err != nil {
		return nil, databaseError(err)
	}
	ctx = annotate(ctx, run)
	if terminal(run.Status) || request.Attempt < run.Attempts {
		return run, nil
	}
	if run.Status != "queued" && run.Status != "retrying" {
		return nil, status.Error(codes.FailedPrecondition, "notification requires an accepted order")
	}
	newState, origin, errorCode, eventID := "retrying", "notification-worker", request.ErrorCode, request.ErrorEventId
	if request.Outcome == "succeeded" {
		newState, origin, errorCode, eventID = "completed", "", "", ""
	}
	if request.Outcome == "dead_lettered" {
		newState = "dead_lettered"
	}
	run, err = scanRun(tx.QueryRow(ctx, `UPDATE fulfillment.runs SET status=$3,attempts=$4,failure_service=$5,error_code=$6,error_event_id=$7,updated_at=now() WHERE run_id=$1 AND session_id=$2 RETURNING `+runColumns, request.RunId, s.sessionID, newState, request.Attempt, origin, errorCode, eventID))
	if err != nil {
		return nil, databaseError(err)
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, databaseError(err)
	}
	fields := runFields(run)
	fields["messaging.message.id"], fields["sentry.event_id"] = request.MessageId, eventID
	s.log.Log(ctx, "INFO", "Notification result recorded", fields)
	return run, nil
}

// Rollback retains trace context but is bounded during request cancellation and shutdown.
func rollback(ctx context.Context, tx pgx.Tx) {
	bounded, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	_ = tx.Rollback(bounded)
}
