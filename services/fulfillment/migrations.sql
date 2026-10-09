-- PostgreSQL checks database CREATE even for CREATE SCHEMA IF NOT EXISTS.
-- Avoid that check when infrastructure has already provisioned the owned schema.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'fulfillment') THEN
    CREATE SCHEMA fulfillment AUTHORIZATION CURRENT_USER;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS fulfillment.runs (
  run_id uuid PRIMARY KEY,
  session_id text NOT NULL,
  scenario text NOT NULL,
  worker_mode text NOT NULL,
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','queued','retrying','completed','failed','dead_lettered')),
  trace_id text NOT NULL,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
  failure_service text NOT NULL DEFAULT '',
  error_code text NOT NULL DEFAULT '',
  error_event_id text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_session_created ON fulfillment.runs(session_id, created_at DESC);
CREATE TABLE IF NOT EXISTS fulfillment.inventory (
  product_id text PRIMARY KEY,
  available_quantity integer NOT NULL CHECK (available_quantity >= 0)
);
INSERT INTO fulfillment.inventory(product_id, available_quantity)
VALUES ('demo-widget', 10000) ON CONFLICT (product_id) DO NOTHING;
CREATE TABLE IF NOT EXISTS fulfillment.orders (
  run_id uuid PRIMARY KEY REFERENCES fulfillment.runs(run_id),
  product_id text NOT NULL REFERENCES fulfillment.inventory(product_id),
  quantity integer NOT NULL CHECK (quantity = 1),
  unit_price_cents bigint NOT NULL CHECK (unit_price_cents > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fulfillment.outbox (
  run_id uuid PRIMARY KEY REFERENCES fulfillment.runs(run_id),
  carrier jsonb NOT NULL,
  published_at timestamptz,
  message_id text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outbox_pending ON fulfillment.outbox(created_at) WHERE published_at IS NULL;
