package main

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/baggage"
)

var workflowKeys = []string{"workshop.run_id", "workshop.session_id", "workshop.scenario", "workshop.step"}

// Only the workshop keys enter application logs and spans; unrelated baggage stays untouched.
func workflowFields(ctx context.Context) map[string]any {
	fields := map[string]any{
		"workshop.session_id": envDefault("WORKSHOP_SESSION_ID", "local-workshop"),
		"workshop.step":       "fulfillment",
	}
	carrier := baggage.FromContext(ctx)
	for _, key := range workflowKeys {
		if value := carrier.Member(key).Value(); value != "" {
			fields[key] = value
		}
	}
	return fields
}

func workflowAttributes(ctx context.Context, step string) []attribute.KeyValue {
	fields := workflowFields(ctx)
	if step != "" {
		fields["workshop.step"] = step
	}
	attributes := make([]attribute.KeyValue, 0, len(fields))
	for key, value := range fields {
		attributes = append(attributes, attribute.String(key, value.(string)))
	}
	return attributes
}

// Store authoritative run metadata in context so SQL spans and downstream carriers retain it.
func withWorkflow(ctx context.Context, runID, sessionID, scenario, step string) context.Context {
	values := map[string]string{
		"workshop.run_id": runID, "workshop.session_id": sessionID,
		"workshop.scenario": scenario, "workshop.step": step,
	}
	carrier := baggage.FromContext(ctx)
	for key, value := range values {
		if value == "" {
			continue
		}
		member, err := baggage.NewMember(key, value)
		if err != nil {
			continue
		}
		carrier, err = carrier.SetMember(member)
		if err != nil {
			continue
		}
	}
	return baggage.ContextWithBaggage(ctx, carrier)
}
