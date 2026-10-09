package main

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

const notificationStream = "workshop:notifications"
const publishedIndex = "workshop:published"

// Atomic dedup means a crash between XADD and PostgreSQL commit cannot create a second message.
var publishScript = redis.NewScript(`
local previous = redis.call('HGET', KEYS[2], ARGV[1])
if previous then return previous end
local id = redis.call('XADD', KEYS[1], '*',
 'run_id', ARGV[1], 'session_id', ARGV[2], 'scenario', ARGV[3], 'worker_mode', ARGV[4],
 'traceparent', ARGV[5], 'tracestate', ARGV[6], 'baggage', ARGV[7])
redis.call('HSET', KEYS[2], ARGV[1], id)
return id
`)

func (s *Service) publishLoop(ctx context.Context) {
	timer := time.NewTicker(250 * time.Millisecond)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
			for count := 0; count < 20 && ctx.Err() == nil; count++ {
				published, err := s.publishOne(ctx)
				if err != nil {
					if ctx.Err() == nil {
						s.log.Log(ctx, "WARN", "Notification outbox will retry", map[string]any{"workshop.session_id": s.sessionID, "workshop.step": "notification.outbox_poll", "error.code": "outbox_publish_failed"})
					}
					break
				}
				if !published {
					break
				}
			}
		}
	}
}

func (s *Service) publishOne(parent context.Context) (bool, error) {
	ctx, cancel := context.WithTimeout(parent, 3*time.Second)
	defer cancel()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer rollback(ctx, tx)
	var runID, sessionID, scenario, workerMode string
	var stored []byte
	err = tx.QueryRow(ctx, `SELECT o.run_id::text,r.session_id,r.scenario,r.worker_mode,o.carrier FROM fulfillment.outbox o JOIN fulfillment.runs r ON r.run_id=o.run_id WHERE o.published_at IS NULL AND r.session_id=$1 ORDER BY o.created_at LIMIT 1 FOR UPDATE OF o SKIP LOCKED`, s.sessionID).Scan(&runID, &sessionID, &scenario, &workerMode, &stored)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	carrier := propagation.MapCarrier{}
	if err := json.Unmarshal(stored, &carrier); err != nil {
		return false, err
	}
	ctx = otel.GetTextMapPropagator().Extract(ctx, carrier)
	ctx = withWorkflow(ctx, runID, sessionID, scenario, "notification.publish")
	ctx, span := otel.Tracer("workshop.fulfillment").Start(ctx, "notifications publish", trace.WithSpanKind(trace.SpanKindProducer), trace.WithAttributes(
		attribute.String("messaging.system", "redis"), attribute.String("messaging.destination.name", notificationStream), attribute.String("messaging.operation.name", "send"), attribute.String("messaging.operation.type", "send"),
		attribute.String("workshop.run_id", runID), attribute.String("workshop.session_id", sessionID), attribute.String("workshop.scenario", scenario), attribute.String("workshop.step", "notification.publish"), attribute.String("workshop.worker_mode", workerMode),
	))
	defer span.End()
	envelope := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, envelope)
	redisAttributes := append(workflowAttributes(ctx, "redis.publish"), attribute.String("db.system.name", "redis"), attribute.String("db.operation.name", "EVAL"))
	redisCtx, redisSpan := otel.Tracer("workshop.fulfillment.redis").Start(ctx, "redis EVAL publish", trace.WithSpanKind(trace.SpanKindClient), trace.WithAttributes(redisAttributes...))
	messageID, err := publishScript.Run(redisCtx, s.redis, []string{notificationStream, publishedIndex}, runID, sessionID, scenario, workerMode, envelope.Get("traceparent"), envelope.Get("tracestate"), envelope.Get("baggage")).Text()
	if err != nil {
		redisSpan.RecordError(err)
		redisSpan.SetStatus(codes.Error, "Redis publication failed")
	}
	redisSpan.End()
	if err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "publication will retry")
		s.log.Log(ctx, "WARN", "Redis publication failed; durable outbox retained", map[string]any{"workshop.run_id": runID, "workshop.session_id": sessionID, "workshop.scenario": scenario})
		return false, err
	}
	span.SetAttributes(attribute.String("messaging.message.id", messageID))
	if _, err := tx.Exec(ctx, `UPDATE fulfillment.outbox SET published_at=now(),message_id=$2 WHERE run_id=$1`, runID, messageID); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "outbox acknowledgment failed")
		return false, err
	}
	if err := tx.Commit(ctx); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "outbox commit failed")
		return false, err
	}
	s.log.Log(ctx, "INFO", "Notification published with original workflow trace", map[string]any{"workshop.run_id": runID, "workshop.session_id": sessionID, "workshop.scenario": scenario, "workshop.worker_mode": workerMode, "messaging.message.id": messageID})
	return true, nil
}
