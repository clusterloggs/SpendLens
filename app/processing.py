from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy.orm import Session

from .config import ARTIFACT_DIR, DEFAULT_CURRENCY
from .database import SessionLocal
from .image_quality import analyze_image
from .models import ProcessingLog, Receipt, ReceiptItem, ReceiptPayment, Store, utc_now
from .ocr import OCRUnavailable, run_ocr
from .parser import parse_receipt_text
from .utils import money_to_decimal, normalize_store_name, parse_decimal, parse_iso_date, parse_iso_time, titleish
from .validation import validate_extraction


def process_receipt_job(receipt_id: str, manual_ocr_text: str | None = None) -> None:
    db = SessionLocal()
    try:
        process_receipt(db, receipt_id, manual_ocr_text)
    finally:
        db.close()


def process_receipt(db: Session, receipt_id: str, manual_ocr_text: str | None = None) -> None:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        return

    started = time.perf_counter()
    receipt.status = "processing"
    receipt.updated_at = utc_now()
    log_event(db, receipt.id, "processing", "running", "Processing started.")
    db.commit()

    file_path = Path(receipt.image_path)
    quality = analyze_image(file_path)
    if quality.get("warnings"):
        log_event(db, receipt.id, "quality", "warning", "; ".join(quality["warnings"]))

    try:
        ocr_result = run_ocr(file_path, receipt.content_type, manual_ocr_text)
    except OCRUnavailable as exc:
        receipt.status = "ocr_failed"
        receipt.validation_message = str(exc)
        receipt.processed_at = utc_now()
        receipt.updated_at = utc_now()
        log_event(db, receipt.id, "ocr", "failed", str(exc))
        db.commit()
        return

    write_artifact(receipt.id, "ocr_text.txt", ocr_result.text)
    extraction = ocr_result.metadata.get("structured_extraction") if ocr_result.metadata else None
    if not extraction or not extraction.get("items"):
        extraction = parse_receipt_text(ocr_result.text, receipt.currency_code or DEFAULT_CURRENCY)
    elif not extraction.get("currency_code"):
        extraction["currency_code"] = receipt.currency_code or DEFAULT_CURRENCY
    status, validation_errors, _overall_confidence = validate_extraction(extraction)
    write_artifact(receipt.id, "parsed_extraction.json", json.dumps(extraction, indent=2, default=str))

    replace_extracted_rows(db, receipt.id)
    store = upsert_store(db, extraction.get("store_raw_name"), extraction.get("store_address"), extraction.get("store_phone"))
    apply_receipt_fields(receipt, extraction, store.id if store else None, status, validation_errors, ocr_result.text)
    insert_extracted_rows(db, receipt.id, extraction)

    elapsed = int((time.perf_counter() - started) * 1000)
    receipt.processed_at = utc_now()
    receipt.updated_at = utc_now()
    log_event(db, receipt.id, "processing", status, f"Processing finished in {elapsed} ms.")
    db.commit()


def replace_extracted_rows(db: Session, receipt_id: str) -> None:
    for model in (ReceiptItem, ReceiptPayment):
        db.query(model).filter(model.receipt_id == receipt_id).delete()


def upsert_store(db: Session, raw_name: str | None, address: str | None = None, phone: str | None = None) -> Store | None:
    normalized = normalize_store_name(raw_name)
    if not normalized:
        return None

    store = db.query(Store).filter(Store.normalized_name == normalized).first()
    if store:
        if address and not store.address:
            store.address = address
        if phone and not store.phone:
            store.phone = phone
        store.updated_at = utc_now()
        return store

    store = Store(
        name=titleish(raw_name) or raw_name or "Unknown Store",
        normalized_name=normalized,
        address=address,
        phone=phone,
    )
    db.add(store)
    db.flush()
    return store


def apply_receipt_fields(receipt: Receipt, extraction: dict, store_id: str | None, status: str, validation_errors: list, raw_text: str) -> None:
    receipt.store_id = store_id
    receipt.status = status
    receipt.ticket_number = extraction.get("transaction_id")
    receipt.receipt_date = parse_iso_date(extraction.get("receipt_date"))
    receipt.receipt_time = parse_iso_time(extraction.get("receipt_time"))
    receipt.customer_name = extraction.get("customer_name")
    receipt.seller = extraction.get("seller") or extraction.get("cashier_name")
    receipt.currency_code = extraction.get("currency_code") or receipt.currency_code or DEFAULT_CURRENCY
    receipt.subtotal_amount = money_to_decimal(extraction.get("subtotal_amount"))
    receipt.tax_amount = money_to_decimal(extraction.get("tax_amount"))
    receipt.discount_amount = money_to_decimal(extraction.get("discount_amount"))
    receipt.total_amount = money_to_decimal(extraction.get("total_amount"))
    receipt.raw_ocr_text = raw_text
    receipt.validation_message = "; ".join(f"{error['code']}: {error['message']}" for error in validation_errors) if validation_errors else None


def insert_extracted_rows(db: Session, receipt_id: str, extraction: dict) -> None:
    for item in extraction.get("items", []):
        db.add(
            ReceiptItem(
                receipt_id=receipt_id,
                line_number=item["line_number"],
                item_name=item.get("item_name_clean") or "Unknown item",
                quantity=parse_decimal(item.get("quantity")) or 1,
                unit_price=money_to_decimal(item.get("unit_price_amount")),
                total_price=money_to_decimal(item.get("total_price_amount")),
            )
        )

    for payment in extraction.get("payments", []):
        db.add(
            ReceiptPayment(
                receipt_id=receipt_id,
                method=payment.get("method"),
                amount=money_to_decimal(payment.get("amount")),
                change_amount=money_to_decimal(payment.get("change_amount")),
            )
        )


def write_artifact(receipt_id: str, name: str, content: str) -> str:
    receipt_dir = ARTIFACT_DIR / receipt_id
    receipt_dir.mkdir(parents=True, exist_ok=True)
    path = receipt_dir / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def log_event(db: Session, receipt_id: str, stage: str, status: str, message: str | None = None) -> None:
    db.add(ProcessingLog(receipt_id=receipt_id, stage=stage, status=status, message=message))


def record_correction(db: Session, receipt_id: str, field_path: str, old_value, new_value, item_id: str | None = None) -> None:
    message = f"{field_path}: {old_value!s} -> {new_value!s}"
    log_event(db, receipt_id, "review", "corrected", message)
