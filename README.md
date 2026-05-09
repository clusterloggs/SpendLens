# Grocery Receipt Scanner

Runnable MVP implementation of the grocery receipt scanning app.

## Current MVP Scope

The app now uses the lean five-table design:

- `stores`
- `receipts`
- `receipt_items`
- `receipt_payments`
- `processing_logs`

`receipts.id` is the internal primary key. Ticket numbers, image names, image hashes, and log IDs are metadata, not relationship keys.

## What Is Implemented

- FastAPI backend with upload-session, raw file upload, processing, retry, detail, review, approval, log, and CSV export endpoints.
- SQLAlchemy data model with SQLite for local development.
- Static browser UI for upload, status polling, extracted table review, item edits, approval, retry, and CSV export.
- OCR abstraction with:
  - plain text receipt upload support,
  - optional OCR text override for local development,
  - AWS Textract `AnalyzeExpense` support for production receipt extraction,
  - automatic Tesseract CLI support when `tesseract` is installed on PATH.
- Heuristic receipt parser for store, phone, address, date, time, ticket number, customer, seller, items, totals, transfer/card/cash payments, and change.
- Validation for missing fields, line-item reconciliation, total reconciliation, future dates, and confidence thresholds.
- Parser/API tests and a sample grocery receipt fixture.

## Run Locally

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

The local MVP database is created at:

```text
data/receipts_mvp.db
```

Uploaded files and extraction artifacts are stored under:

```text
data/uploads
data/artifacts
```

## OCR Recommendation

For production, use **Amazon Textract AnalyzeExpense**.

Why:

- It is designed for invoices and receipts.
- It returns standardized receipt fields such as receipt date, receipt number, vendor name, vendor phone, subtotal, tax, total, amount paid, item description, quantity, unit price, and item price.
- That output maps directly to the MVP tables with less custom parsing than raw OCR.

## AWS Textract Setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

Set the provider:

```powershell
$env:OCR_PROVIDER="aws_textract"
```

Configure AWS credentials using one of the standard AWS SDK methods:

```powershell
$env:AWS_PROFILE="your-profile"
$env:AWS_REGION="us-east-1"
```

or:

```powershell
$env:AWS_ACCESS_KEY_ID="..."
$env:AWS_SECRET_ACCESS_KEY="..."
$env:AWS_REGION="us-east-1"
```

The IAM principal needs permission for:

```text
textract:AnalyzeExpense
```

Current behavior:

- Image uploads are sent to Textract as bytes using `AnalyzeExpense`.
- Textract `SummaryFields` map to `stores`, `receipts`, and `receipt_payments`.
- Textract `LineItemGroups` map to `receipt_items`.
- If AWS is not enabled, the app still supports `.txt` uploads, manual OCR text, and Tesseract when installed.

Good alternatives:

- **Azure AI Document Intelligence `prebuilt-receipt`** if the project is already on Azure.
- **Google Document AI Expense Parser** if the project is already on Google Cloud.
- **Google Cloud Vision `DOCUMENT_TEXT_DETECTION`** only if you want raw OCR text and are comfortable owning more custom parsing logic.
- **Tesseract** for local/offline experiments, but not as the main production OCR for blurry mobile receipt photos.

## API Examples

Create an upload session:

```http
POST /api/receipts/uploads
Content-Type: application/json

{
  "file_name": "receipt.txt",
  "content_type": "text/plain",
  "file_size_bytes": 512,
  "currency_code": "NGN"
}
```

Upload bytes:

```http
PUT /api/receipts/{receipt_id}/file
Content-Type: text/plain
```

Start processing:

```http
POST /api/receipts/{receipt_id}/process
Content-Type: application/json

{
  "ocr_text": ""
}
```

Export line items:

```http
GET /api/exports/receipt-items.csv
```

## Test

```powershell
python -m unittest discover -s tests
```

## Production Next Steps

- Swap SQLite for PostgreSQL using `infra/postgres_schema.sql`.
- Add authentication and per-user authorization.
- Add object storage instead of local file paths.
- Add a real queue worker for background processing.
- Add duplicate detection using `image_hash` plus `store_id + ticket_number`.
