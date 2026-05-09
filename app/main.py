from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import (
    DEFAULT_CURRENCY,
    MAX_DOCUMENT_UPLOAD_BYTES,
    MAX_IMAGE_UPLOAD_BYTES,
    ROOT_DIR,
    SUPPORTED_CONTENT_TYPES,
    UPLOAD_DIR,
    ensure_runtime_dirs,
)
from .database import get_db, init_db
from .models import ProcessingLog, Receipt, ReceiptItem, Store, utc_now
from .processing import process_receipt_job, record_correction
from .utils import (
    decimalish,
    money_to_decimal,
    normalize_store_name,
    parse_decimal,
    parse_iso_date,
    parse_iso_time,
    sanitize_filename,
    serialize_datetime,
    sha256_bytes,
    titleish,
)


ensure_runtime_dirs()
init_db()

app = FastAPI(
    title="Grocery Receipt Scanner",
    version="1.1.0",
    description="MVP grocery receipt scanner with four core tables.",
)

FRONTEND_DIR = ROOT_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "receipt-scanner", "schema": "mvp", "time": utc_now().isoformat()}


@app.post("/api/receipts/uploads")
def create_upload_session(payload: dict = Body(default={}), db: Session = Depends(get_db)) -> dict[str, Any]:
    file_name = sanitize_filename(payload.get("file_name"), "receipt")
    content_type = payload.get("content_type") or "application/octet-stream"
    file_size = int(payload.get("file_size_bytes") or 0)

    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {content_type}")
    max_size = MAX_DOCUMENT_UPLOAD_BYTES if content_type == "application/pdf" else MAX_IMAGE_UPLOAD_BYTES
    if file_size and file_size > max_size:
        raise HTTPException(status_code=413, detail=f"File exceeds limit of {max_size} bytes.")

    receipt = Receipt(
        status="created",
        original_file_name=file_name,
        content_type=content_type,
        currency_code=payload.get("currency_code") or DEFAULT_CURRENCY,
        image_path=str(upload_path_for("pending", file_name)),
    )
    db.add(receipt)
    db.flush()

    target_path = upload_path_for(receipt.id, file_name)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    receipt.image_path = str(target_path)
    receipt.status = "awaiting_upload"
    db.add(ProcessingLog(receipt_id=receipt.id, stage="upload", status="created", message="Upload session created."))
    db.commit()

    return {
        "receipt_id": receipt.id,
        "upload_url": f"/api/receipts/{receipt.id}/file",
        "method": "PUT",
        "max_file_size_bytes": max_size,
        "required_headers": {"Content-Type": content_type},
    }


@app.put("/api/receipts/{receipt_id}/file")
async def upload_receipt_file(receipt_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")

    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    content_type = request.headers.get("content-type", receipt.content_type or "application/octet-stream").split(";")[0]
    max_size = MAX_DOCUMENT_UPLOAD_BYTES if content_type == "application/pdf" else MAX_IMAGE_UPLOAD_BYTES
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"File exceeds limit of {max_size} bytes.")

    path = Path(receipt.image_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    receipt.content_type = content_type
    receipt.image_hash = sha256_bytes(content)
    receipt.status = "uploaded"
    receipt.updated_at = utc_now()
    db.add(ProcessingLog(receipt_id=receipt.id, stage="upload", status="completed", message="File uploaded."))
    db.commit()
    return {"receipt_id": receipt.id, "status": receipt.status, "sha256": receipt.image_hash}


@app.post("/api/receipts/{receipt_id}/process")
def process_receipt_endpoint(
    receipt_id: str,
    background_tasks: BackgroundTasks,
    payload: dict | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    if not Path(receipt.image_path).exists():
        raise HTTPException(status_code=400, detail="Receipt file has not been uploaded yet.")

    manual_text = (payload or {}).get("ocr_text")
    receipt.status = "queued"
    receipt.queued_at = utc_now()
    receipt.updated_at = utc_now()
    db.add(ProcessingLog(receipt_id=receipt.id, stage="queue", status="queued", message="Processing queued."))
    db.commit()

    background_tasks.add_task(process_receipt_job, receipt_id, manual_text)
    return {"receipt_id": receipt_id, "status": "queued"}


@app.post("/api/receipts/{receipt_id}/retry")
def retry_receipt(
    receipt_id: str,
    background_tasks: BackgroundTasks,
    payload: dict | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return process_receipt_endpoint(receipt_id, background_tasks, payload, db)


@app.get("/api/receipts")
def list_receipts(status: str | None = None, limit: int = 50, db: Session = Depends(get_db)) -> dict[str, Any]:
    query = db.query(Receipt).order_by(Receipt.created_at.desc())
    if status:
        query = query.filter(Receipt.status == status)
    receipts = query.limit(min(max(limit, 1), 200)).all()
    return {"receipts": [serialize_receipt_summary(receipt) for receipt in receipts]}


@app.get("/api/receipts/{receipt_id}")
def get_receipt(receipt_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return serialize_receipt_detail(receipt)


@app.get("/api/receipts/{receipt_id}/file")
def get_receipt_file(receipt_id: str, db: Session = Depends(get_db)) -> FileResponse:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    path = Path(receipt.image_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Receipt file is missing.")
    return FileResponse(path, media_type=receipt.content_type, filename=receipt.original_file_name)


@app.get("/api/receipts/{receipt_id}/events")
def get_receipt_events(receipt_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not db.get(Receipt, receipt_id):
        raise HTTPException(status_code=404, detail="Receipt not found.")
    logs = db.query(ProcessingLog).filter(ProcessingLog.receipt_id == receipt_id).order_by(ProcessingLog.created_at.asc()).all()
    return {"events": [serialize_log(log) for log in logs]}


@app.patch("/api/receipts/{receipt_id}")
def patch_receipt(receipt_id: str, payload: dict = Body(default={}), db: Session = Depends(get_db)) -> dict[str, Any]:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")

    store_name = payload.get("store_name") or payload.get("store_raw_name")
    if store_name:
        old = receipt.store.name if receipt.store else None
        receipt.store = upsert_store(db, store_name)
        if old != store_name:
            record_correction(db, receipt.id, "store.name", old, store_name)

    allowed = {
        "ticket_number",
        "receipt_date",
        "receipt_time",
        "customer_name",
        "seller",
        "currency_code",
        "total_amount",
    }
    alias_map = {"transaction_id": "ticket_number", "cashier_name": "seller"}
    for incoming_field, value in payload.items():
        field = alias_map.get(incoming_field, incoming_field)
        if field not in allowed:
            continue
        old = getattr(receipt, field)
        if field.endswith("_amount"):
            value = money_to_decimal(value)
        elif field == "receipt_date":
            value = parse_iso_date(value)
        elif field == "receipt_time":
            value = parse_iso_time(value)
        setattr(receipt, field, value)
        if old != value:
            record_correction(db, receipt.id, field, old, value)

    if receipt.status not in {"approved", "validated"}:
        receipt.status = "needs_review"
    receipt.updated_at = utc_now()
    db.commit()
    return serialize_receipt_detail(receipt)


@app.patch("/api/receipt-items/{item_id}")
def patch_item(item_id: str, payload: dict = Body(default={}), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = db.get(ReceiptItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Receipt item not found.")

    alias_map = {
        "item_name_clean": "item_name",
        "unit_price_amount": "unit_price",
        "total_price_amount": "total_price",
    }
    allowed = {"item_name", "quantity", "unit_price", "total_price"}
    for incoming_field, value in payload.items():
        field = alias_map.get(incoming_field, incoming_field)
        if field not in allowed:
            continue
        old = getattr(item, field)
        if field in {"unit_price", "total_price"}:
            value = money_to_decimal(value)
        elif field == "quantity":
            value = parse_decimal(value)
        setattr(item, field, value)
        if old != value:
            record_correction(db, item.receipt_id, f"items.{item.line_number}.{field}", old, value, item.id)

    item.updated_at = utc_now()
    receipt = db.get(Receipt, item.receipt_id)
    if receipt and receipt.status == "validated":
        receipt.status = "needs_review"
    db.commit()
    return serialize_item(item)


@app.post("/api/receipts/{receipt_id}/approve")
def approve_receipt(receipt_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    if not receipt.items:
        raise HTTPException(status_code=400, detail="Cannot approve a receipt with no parsed items.")
    receipt.status = "approved"
    receipt.approved_at = utc_now()
    receipt.updated_at = utc_now()
    db.add(ProcessingLog(receipt_id=receipt.id, stage="review", status="approved", message="Receipt approved."))
    db.commit()
    return serialize_receipt_detail(receipt)


@app.get("/api/exports/receipt-items.csv")
def export_items_csv(db: Session = Depends(get_db)) -> Response:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "receipt_id",
            "status",
            "receipt_date",
            "receipt_time",
            "store_name",
            "ticket_number",
            "currency_code",
            "line_number",
            "item_name",
            "quantity",
            "unit_price",
            "total_price",
        ]
    )

    rows = (
        db.query(ReceiptItem, Receipt, Store)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .outerjoin(Store, Receipt.store_id == Store.id)
        .filter(Receipt.status.in_(["validated", "approved", "needs_review"]))
        .order_by(Receipt.created_at.desc(), ReceiptItem.line_number.asc())
        .all()
    )
    for item, receipt, store in rows:
        writer.writerow(
            [
                receipt.id,
                receipt.status,
                receipt.receipt_date,
                receipt.receipt_time,
                store.name if store else None,
                receipt.ticket_number,
                receipt.currency_code,
                item.line_number,
                item.item_name,
                item.quantity,
                item.unit_price,
                item.total_price,
            ]
        )

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=receipt-items.csv"},
    )


def upload_path_for(receipt_id: str, file_name: str) -> Path:
    return UPLOAD_DIR / receipt_id / sanitize_filename(file_name)


def upsert_store(db: Session, name: str) -> Store:
    normalized = normalize_store_name(name)
    store = db.query(Store).filter(Store.normalized_name == normalized).first()
    if store:
        store.name = titleish(name) or name
        store.updated_at = utc_now()
        return store
    store = Store(name=titleish(name) or name, normalized_name=normalized)
    db.add(store)
    db.flush()
    return store


def serialize_receipt_summary(receipt: Receipt) -> dict[str, Any]:
    return {
        "id": receipt.id,
        "status": receipt.status,
        "store_name": receipt.store.name if receipt.store else None,
        "store_raw_name": receipt.store.name if receipt.store else None,
        "ticket_number": receipt.ticket_number,
        "transaction_id": receipt.ticket_number,
        "receipt_date": serialize_date(receipt.receipt_date),
        "total_amount": decimalish(receipt.total_amount),
        "currency_code": receipt.currency_code,
        "item_count": len(receipt.items),
        "overall_confidence": None,
        "created_at": serialize_datetime(receipt.created_at),
    }


def serialize_receipt_detail(receipt: Receipt) -> dict[str, Any]:
    return {
        **serialize_receipt_summary(receipt),
        "original_file_name": receipt.original_file_name,
        "content_type": receipt.content_type,
        "source_file_sha256": receipt.image_hash,
        "image_hash": receipt.image_hash,
        "image_path": receipt.image_path,
        "file_url": f"/api/receipts/{receipt.id}/file",
        "receipt_time": serialize_time(receipt.receipt_time),
        "customer_name": receipt.customer_name,
        "seller": receipt.seller,
        "timezone": None,
        "total_amount": decimalish(receipt.total_amount),
        "validation_message": receipt.validation_message,
        "validation_errors": validation_errors_from_message(receipt.validation_message),
        "raw_text": receipt.raw_ocr_text,
        "raw_ocr_text": receipt.raw_ocr_text,
        "items": [serialize_item(item) for item in receipt.items],
        "events": [serialize_log(log) for log in reversed(receipt.logs[-20:])],
        "pages": [],
        "created_at": serialize_datetime(receipt.created_at),
        "queued_at": serialize_datetime(receipt.queued_at),
        "processed_at": serialize_datetime(receipt.processed_at),
        "approved_at": serialize_datetime(receipt.approved_at),
        "updated_at": serialize_datetime(receipt.updated_at),
    }


def serialize_item(item: ReceiptItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "receipt_id": item.receipt_id,
        "line_number": item.line_number,
        "item_name": item.item_name,
        "item_name_clean": item.item_name,
        "quantity": decimalish(item.quantity),
        "unit_price": decimalish(item.unit_price),
        "unit_price_amount": decimalish(item.unit_price),
        "total_price": decimalish(item.total_price),
        "total_price_amount": decimalish(item.total_price),
        "confidence": 1.0,
        "review_required": False,
    }


def serialize_log(log: ProcessingLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "event_type": log.stage,
        "stage": log.stage,
        "status": log.status,
        "message": log.message,
        "duration_ms": None,
        "details": {},
        "created_at": serialize_datetime(log.created_at),
    }


def validation_errors_from_message(message: str | None) -> list[dict[str, str]]:
    if not message:
        return []
    return [{"code": "VALIDATION", "message": part.strip(), "severity": "warning"} for part in message.split(";") if part.strip()]


def serialize_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()[:10]


def serialize_time(value: time | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


@app.exception_handler(Exception)
async def generic_exception_handler(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
