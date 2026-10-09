DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'catalog') THEN
    EXECUTE 'CREATE SCHEMA catalog AUTHORIZATION CURRENT_USER';
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS catalog.products (
  product_id text PRIMARY KEY,
  name text NOT NULL,
  unit_price_cents bigint NOT NULL CHECK (unit_price_cents >= 0)
);
INSERT INTO catalog.products (product_id,name,unit_price_cents)
VALUES ('demo-widget','Demo widget',1299)
ON CONFLICT (product_id) DO UPDATE SET name=EXCLUDED.name,unit_price_cents=EXCLUDED.unit_price_cents;
