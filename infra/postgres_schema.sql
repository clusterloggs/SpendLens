CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE stores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  address TEXT,
  phone TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  store_id UUID REFERENCES stores(id),

  ticket_number TEXT,
  original_file_name TEXT,
  image_path TEXT NOT NULL,
  image_hash CHAR(64),
  content_type TEXT,

  receipt_date DATE,
  receipt_time TIME,
  customer_name TEXT,
  seller TEXT,

  currency_code CHAR(3) NOT NULL DEFAULT 'USD',
  subtotal_amount NUMERIC(12, 2),
  tax_amount NUMERIC(12, 2),
  discount_amount NUMERIC(12, 2),
  total_amount NUMERIC(12, 2),

  raw_ocr_text TEXT,
  status TEXT NOT NULL DEFAULT 'created',
  validation_message TEXT,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  queued_at TIMESTAMPTZ,
  processed_at TIMESTAMPTZ,
  approved_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE receipt_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  receipt_id UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
  line_number INTEGER NOT NULL,
  item_name TEXT NOT NULL,
  quantity NUMERIC(12, 3) NOT NULL DEFAULT 1,
  unit_price NUMERIC(12, 2),
  total_price NUMERIC(12, 2),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (receipt_id, line_number)
);

CREATE TABLE receipt_payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  receipt_id UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
  method TEXT,
  amount NUMERIC(12, 2),
  change_amount NUMERIC(12, 2),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE processing_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  receipt_id UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_stores_normalized_name ON stores (normalized_name);
CREATE INDEX idx_receipts_store_date ON receipts (store_id, receipt_date DESC);
CREATE INDEX idx_receipts_ticket_number ON receipts (ticket_number);
CREATE INDEX idx_receipts_image_hash ON receipts (image_hash);
CREATE INDEX idx_receipts_status ON receipts (status);
CREATE INDEX idx_receipt_items_receipt ON receipt_items (receipt_id);
CREATE INDEX idx_receipt_items_name ON receipt_items (item_name);
CREATE INDEX idx_receipt_payments_receipt ON receipt_payments (receipt_id);
CREATE INDEX idx_processing_logs_receipt ON processing_logs (receipt_id, created_at);

CREATE VIEW receipt_line_items_flat AS
SELECT
  r.id AS receipt_id,
  r.status,
  r.receipt_date,
  r.receipt_time,
  s.name AS store_name,
  r.ticket_number,
  r.currency_code,
  i.line_number,
  i.item_name,
  i.quantity,
  i.unit_price,
  i.total_price
FROM receipts r
LEFT JOIN stores s ON s.id = r.store_id
JOIN receipt_items i ON i.receipt_id = r.id
WHERE r.status IN ('validated', 'approved', 'needs_review');
