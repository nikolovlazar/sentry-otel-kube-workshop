package main

import (
	"context"
	_ "embed"
	"errors"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/getsentry/sentry-go"
	"github.com/jackc/pgx/v5/pgxpool"
	workshopv1 "github.com/nikolovlazar/sentry-otel-kube-workshop/services/fulfillment/gen/workshop/v1"
	"github.com/redis/go-redis/v9"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	"google.golang.org/grpc/health/grpc_health_v1"
)

//go:embed migrations.sql
var migrations string

func main() {
	log := newLogger(envDefault("OTEL_SERVICE_NAME", "fulfillment"))
	if err := serve(log); err != nil {
		log.Log(context.Background(), "ERROR", "Fulfillment stopped", map[string]any{"error.code": err.Error()})
		os.Exit(1)
	}
}

func serve(log *Logger) error {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	for _, key := range []string{"DATABASE_URL", "REDIS_URL", "WORKSHOP_SESSION_ID", "OTEL_EXPORTER_OTLP_ENDPOINT"} {
		if os.Getenv(key) == "" {
			return errors.New("required_configuration_missing")
		}
	}
	provider, err := setupTelemetry(ctx, log.service)
	if err != nil {
		return errors.New("telemetry_initialization_failed")
	}
	defer func() {
		shutdown, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		_ = provider.Shutdown(shutdown)
		sentry.Flush(3 * time.Second)
	}()
	config, err := pgxpool.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		return errors.New("database_configuration_invalid")
	}
	config.MaxConns = 8
	config.ConnConfig.Tracer = queryTracer{}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("database_initialization_failed")
	}
	defer pool.Close()
	startup, stopStartup := context.WithTimeout(ctx, 20*time.Second)
	defer stopStartup()
	if err := pool.Ping(startup); err != nil {
		return errors.New("database_unavailable")
	}
	if _, err := pool.Exec(startup, migrations); err != nil {
		return errors.New("database_migration_failed")
	}
	options, err := redis.ParseURL(os.Getenv("REDIS_URL"))
	if err != nil {
		return errors.New("redis_configuration_invalid")
	}
	options.DialTimeout, options.ReadTimeout, options.WriteTimeout = time.Second, time.Second, time.Second
	client := redis.NewClient(options)
	defer client.Close()
	service := &Service{pool: pool, redis: client, log: log, sessionID: os.Getenv("WORKSHOP_SESSION_ID")}
	server := grpc.NewServer(grpc.UnaryInterceptor(rpcInterceptor(log)))
	workshopv1.RegisterFulfillmentServer(server, service)
	healthService := health.NewServer()
	grpc_health_v1.RegisterHealthServer(server, healthService)
	healthService.SetServingStatus("", grpc_health_v1.HealthCheckResponse_SERVING)
	listener, err := net.Listen("tcp", ":50051")
	if err != nil {
		return errors.New("grpc_listener_failed")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
		ping, stop := context.WithTimeout(r.Context(), time.Second)
		defer stop()
		if pool.Ping(ping) != nil || client.Ping(ping).Err() != nil {
			http.Error(w, "dependency unavailable", http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ready\n"))
	})
	healthServer := &http.Server{Addr: ":8081", Handler: mux, ReadHeaderTimeout: 2 * time.Second}
	failures := make(chan error, 2)
	go func() { failures <- server.Serve(listener) }()
	go func() { failures <- healthServer.ListenAndServe() }()
	outboxDone := make(chan struct{})
	go func() { defer close(outboxDone); service.publishLoop(ctx) }()
	log.Log(ctx, "INFO", "Fulfillment is ready", map[string]any{"workshop.session_id": service.sessionID})
	select {
	case <-ctx.Done():
	case <-failures:
		cancel()
	}
	healthService.Shutdown()
	shutdown, stop := context.WithTimeout(context.Background(), 5*time.Second)
	defer stop()
	_ = healthServer.Shutdown(shutdown)
	grpcDone := make(chan struct{})
	go func() { server.GracefulStop(); close(grpcDone) }()
	select {
	case <-grpcDone:
	case <-shutdown.Done():
		server.Stop()
	}
	<-outboxDone
	return nil
}

func envDefault(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
