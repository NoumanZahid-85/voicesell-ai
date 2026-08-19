-- ═══════════════════════════════════════════════════════════════════
-- VoiceSell AI — PostgreSQL Schema Migration (Phase 1)
-- Run this against Supabase SQL Editor or via psql for initial setup.
-- ═══════════════════════════════════════════════════════════════════

-- Enable UUID extension (Supabase has this by default)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Enum Types ─────────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE order_status AS ENUM (
        'pending', 'confirmed', 'shipped', 'delivered', 'cancelled'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ── Product Categories ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS product_categories (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL UNIQUE,
    parent_id   UUID REFERENCES product_categories(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_product_categories_name ON product_categories(name);

-- ── Products ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS products (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    category_id     UUID REFERENCES product_categories(id) ON DELETE SET NULL,
    price           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    stock_quantity  INTEGER NOT NULL DEFAULT 0,
    weight_kg       DOUBLE PRECISION,
    dimensions_json JSONB,
    image_url       VARCHAR(512),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_products_name ON products(name);
CREATE INDEX IF NOT EXISTS ix_products_category ON products(category_id);

-- ── Customers ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS customers (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email        VARCHAR(255) NOT NULL UNIQUE,
    name         VARCHAR(255) NOT NULL,
    auth_user_id VARCHAR(255) UNIQUE,  -- FK to Supabase Auth
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_customers_email ON customers(email);

-- ── Orders ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id     UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status          order_status NOT NULL DEFAULT 'pending',
    total_amount    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    idempotency_key VARCHAR(64) UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_orders_customer_status ON orders(customer_id, status);

-- ── Order Items ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS order_items (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id    UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity    INTEGER NOT NULL,
    unit_price  DOUBLE PRECISION NOT NULL,
    subtotal    DOUBLE PRECISION NOT NULL
);

-- ── Product Associations (for upsell, Phase 5) ────────────────────

CREATE TABLE IF NOT EXISTS product_associations (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_a_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    product_b_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    confidence   DOUBLE PRECISION NOT NULL,
    support      DOUBLE PRECISION NOT NULL,
    lift         DOUBLE PRECISION NOT NULL,
    UNIQUE(product_a_id, product_b_id)
);

CREATE INDEX IF NOT EXISTS ix_assoc_product_a ON product_associations(product_a_id);

-- ── Row Level Security (Supabase) ──────────────────────────────────
-- Enable RLS on all tables. Policies will be refined in Phase 8.

ALTER TABLE product_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_associations ENABLE ROW LEVEL SECURITY;

-- Allow the service_role (backend) full access
CREATE POLICY "Service role full access" ON product_categories FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON products FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON customers FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON orders FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON order_items FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON product_associations FOR ALL USING (true) WITH CHECK (true);

-- ── Updated_at trigger ─────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
