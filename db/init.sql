-- ---------------------------------------------------------------------------
-- Schema + change-notification wiring for the real-time orders service.
--
-- Design note: change events are sourced FROM THE DATABASE, not from the API.
-- A trigger fires on every INSERT/UPDATE/DELETE and calls pg_notify(), so any
-- change is captured regardless of who made it (our API, another service, a
-- manual psql session, a migration...). The backend just LISTENs on the channel.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS orders (
    id           SERIAL PRIMARY KEY,
    customer_name TEXT        NOT NULL,
    product_name  TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'shipped', 'delivered')),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keep updated_at fresh on every UPDATE, without trusting the caller to set it.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS orders_set_updated_at ON orders;
CREATE TRIGGER orders_set_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Notify on any change. Payload is JSON: { operation, id, data }.
--
-- Caveat handled here: pg_notify payloads are capped at 8000 bytes. Our rows
-- are tiny so we send the whole row; for wide/large rows you would send just
-- the id + operation and let the client fetch the row (notify-then-fetch).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION notify_orders_change() RETURNS trigger AS $$
DECLARE
    rec     RECORD;
    payload JSON;
BEGIN
    IF (TG_OP = 'DELETE') THEN
        rec := OLD;
    ELSE
        rec := NEW;
    END IF;

    payload := json_build_object(
        'operation', TG_OP,          -- INSERT | UPDATE | DELETE
        'id',        rec.id,
        'data',      row_to_json(rec)
    );

    PERFORM pg_notify('orders_changes', payload::text);
    RETURN NULL;  -- AFTER trigger; return value is ignored
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS orders_notify ON orders;
CREATE TRIGGER orders_notify
    AFTER INSERT OR UPDATE OR DELETE ON orders
    FOR EACH ROW EXECUTE FUNCTION notify_orders_change();

-- Seed a little data so the UI isn't empty on first load.
INSERT INTO orders (customer_name, product_name, status) VALUES
    ('Ada Lovelace',   'Mechanical Keyboard', 'pending'),
    ('Alan Turing',    'USB-C Hub',           'shipped'),
    ('Grace Hopper',   'Laptop Stand',        'delivered')
ON CONFLICT DO NOTHING;
