package main

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"sync"
	"time"

	"github.com/google/uuid"
	"go.opentelemetry.io/otel/trace"
)

// JSON stdout is the single log transport. The Collector supplies OTLP log export.
type Logger struct {
	mu      sync.Mutex
	output  io.Writer
	service string
}

func newLogger(service string) *Logger { return &Logger{output: os.Stdout, service: service} }
func (l *Logger) Log(ctx context.Context, severity, body string, fields map[string]any) {
	record := workflowFields(ctx)
	for key, value := range fields {
		record[key] = value
	}
	record["timestamp"] = time.Now().UTC().Format(time.RFC3339Nano)
	record["severity"] = severity
	record["body"] = body
	record["service.name"] = l.service
	record["log.record_id"] = uuid.NewString()
	span := trace.SpanContextFromContext(ctx)
	record["trace_id"] = span.TraceID().String()
	record["span_id"] = span.SpanID().String()
	record["trace_flags"] = int(span.TraceFlags())
	l.mu.Lock()
	defer l.mu.Unlock()
	_ = json.NewEncoder(l.output).Encode(record)
}
