from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote_plus

import boto3


dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
textract = boto3.client("textract")

STORES_TABLE = dynamodb.Table(os.environ["STORES_TABLE"])
RECEIPTS_TABLE = dynamodb.Table(os.environ["RECEIPTS_TABLE"])
ITEMS_TABLE = dynamodb.Table(os.environ["RECEIPT_ITEMS_TABLE"])
PAYMENTS_TABLE = dynamodb.Table(os.environ["RECEIPT_PAYMENTS_TABLE"])
LOGS_TABLE = dynamodb.Table(os.environ["PROCESSING_LOGS_TABLE"])
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "NGN")


def handler(event, _context):
    for record in records_from_event(event):
        bucket = record["bucket"]
        key = record["key"]
        receipt_id = receipt_id_from_key(key)
        if not receipt_id:
            continue
        process_receipt(bucket, key, receipt_id)
    return {"ok": True}


def process_receipt(bucket: str, key: str, receipt_id: str) -> None:
    receipt = RECEIPTS_TABLE.get_item(Key={"id": receipt_id}).get("Item")
    if not receipt:
        return

    update_receipt(receipt_id, {"status": "processing", "updated_at": now_iso()})
    put_log(receipt_id, "processing", "running", "Processing started.")

    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        response = textract.analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})
        extraction = textract_response_to_extraction(response, receipt.get("currency_code") or DEFAULT_CURRENCY)
        persist_extraction(receipt, extraction, head)
        put_log(receipt_id, "processing", extraction["status"], "Processing finished.")
    except Exception as exc:
        update_receipt(receipt_id, {"status": "ocr_failed", "validation_message": str(exc), "processed_at": now_iso(), "updated_at": now_iso()})
        put_log(receipt_id, "ocr", "failed", str(exc))
        raise


def persist_extraction(receipt: dict, extraction: dict, head: dict) -> None:
    receipt_id = receipt["id"]
    store_id = upsert_store(extraction)
    delete_existing_children(receipt_id)

    for item in extraction["items"]:
        ITEMS_TABLE.put_item(
            Item={
                "receipt_id": receipt_id,
                "line_number": Decimal(item["line_number"]),
                "item_name": item["item_name"],
                "quantity": decimal_or_default(item.get("quantity"), "1"),
                "unit_price": decimal_or_none(item.get("unit_price")),
                "total_price": decimal_or_none(item.get("total_price")),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )

    for payment in extraction["payments"]:
        PAYMENTS_TABLE.put_item(
            Item={
                "receipt_id": receipt_id,
                "id": str(uuid.uuid4()),
                "method": payment.get("method"),
                "amount": decimal_or_none(payment.get("amount")),
                "change_amount": decimal_or_none(payment.get("change_amount")),
                "created_at": now_iso(),
            }
        )

    update_receipt(
        receipt_id,
        {
            "store_id": store_id,
            "ticket_number": extraction.get("ticket_number"),
            "receipt_date": extraction.get("receipt_date"),
            "receipt_time": extraction.get("receipt_time"),
            "customer_name": extraction.get("customer_name"),
            "seller": extraction.get("seller"),
            "currency_code": extraction.get("currency_code") or receipt.get("currency_code") or DEFAULT_CURRENCY,
            "subtotal_amount": decimal_or_none(extraction.get("subtotal_amount")),
            "tax_amount": decimal_or_none(extraction.get("tax_amount")),
            "discount_amount": decimal_or_none(extraction.get("discount_amount")),
            "total_amount": decimal_or_none(extraction.get("total_amount")),
            "status": extraction["status"],
            "validation_message": extraction.get("validation_message"),
            "image_hash": object_hash_from_head(head),
            "processed_at": now_iso(),
            "updated_at": now_iso(),
        },
    )


def textract_response_to_extraction(response: dict, fallback_currency: str) -> dict:
    summary = {}
    items = []
    currency = fallback_currency

    for document in response.get("ExpenseDocuments", []):
        for field in document.get("SummaryFields", []):
            field_type = normalize_type(field.get("Type", {}).get("Text"))
            value = field.get("ValueDetection", {}).get("Text")
            detected_currency = field.get("ValueDetection", {}).get("Currency", {}).get("Code")
            if detected_currency and detected_currency != "UNKNOWN":
                currency = detected_currency
            if value:
                summary[field_type] = value
                label = (field.get("LabelDetection", {}).get("Text") or "").upper()
                if "CUSTOMER" in label:
                    summary["CUSTOMER_NAME"] = value
                if "SELLER" in label or "CASHIER" in label:
                    summary["SELLER"] = value
                if "CHANGE" in label:
                    summary["CHANGE"] = value

        for group in document.get("LineItemGroups", []):
            for line_item in group.get("LineItems", []):
                mapped = map_line_item(line_item, len(items) + 1)
                if mapped:
                    items.append(mapped)

    payments = []
    amount_paid = money_string(summary.get("AMOUNT_PAID") or summary.get("TOTAL"))
    if amount_paid or summary.get("PAYMENT_TYPE") or summary.get("CHANGE"):
        payments.append(
            {
                "method": normalize_payment(summary.get("PAYMENT_TYPE")),
                "amount": amount_paid,
                "change_amount": money_string(summary.get("CHANGE")),
            }
        )

    validation_message = None
    status = "validated"
    if not items:
        status = "parse_failed"
        validation_message = "No line items were detected."
    elif not summary.get("TOTAL"):
        status = "needs_review"
        validation_message = "Total amount was not detected."

    return {
        "store_name": summary.get("VENDOR_NAME") or summary.get("SUPPLIER_NAME"),
        "store_address": summary.get("VENDOR_ADDRESS") or summary.get("SUPPLIER_ADDRESS"),
        "store_phone": digits_only(summary.get("VENDOR_PHONE") or summary.get("SUPPLIER_PHONE")),
        "ticket_number": summary.get("INVOICE_RECEIPT_ID") or summary.get("RECEIPT_ID"),
        "receipt_date": normalize_date(summary.get("INVOICE_RECEIPT_DATE") or summary.get("RECEIPT_DATE")),
        "receipt_time": summary.get("PURCHASE_TIME") or summary.get("TRANSACTION_TIME"),
        "customer_name": summary.get("CUSTOMER_NAME"),
        "seller": summary.get("SELLER") or summary.get("CASHIER"),
        "currency_code": currency,
        "subtotal_amount": money_string(summary.get("SUBTOTAL")),
        "tax_amount": money_string(summary.get("TAX") or summary.get("TOTAL_TAX")),
        "discount_amount": normalize_discount(summary.get("DISCOUNT")),
        "total_amount": money_string(summary.get("TOTAL") or summary.get("AMOUNT_DUE")),
        "items": items,
        "payments": payments,
        "status": status,
        "validation_message": validation_message,
    }


def map_line_item(line_item: dict, line_number: int) -> dict | None:
    fields = {}
    for field in line_item.get("LineItemExpenseFields", []):
        field_type = normalize_type(field.get("Type", {}).get("Text"))
        value = field.get("ValueDetection", {}).get("Text")
        if field_type and value:
            fields[field_type] = value

    item_name = fields.get("ITEM") or fields.get("DESCRIPTION") or fields.get("PRODUCT_CODE") or fields.get("OTHER")
    if not item_name:
        return None
    quantity = decimal_or_default(fields.get("QUANTITY"), "1")
    total_price = money_string(fields.get("PRICE") or fields.get("AMOUNT"))
    unit_price = money_string(fields.get("UNIT_PRICE"))
    if not unit_price and total_price and quantity:
        try:
            unit_price = str((Decimal(total_price) / quantity).quantize(Decimal("0.01")))
        except (InvalidOperation, ZeroDivisionError):
            unit_price = None
    return {
        "line_number": line_number,
        "item_name": item_name,
        "quantity": str(quantity),
        "unit_price": unit_price,
        "total_price": total_price,
    }


def upsert_store(extraction: dict) -> str | None:
    name = extraction.get("store_name")
    if not name:
        return None
    normalized = normalize_store_name(name)
    store_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    STORES_TABLE.put_item(
        Item={
            "id": store_id,
            "name": name,
            "normalized_name": normalized,
            "address": extraction.get("store_address"),
            "phone": extraction.get("store_phone"),
            "updated_at": now_iso(),
            "created_at": now_iso(),
        }
    )
    return store_id


def delete_existing_children(receipt_id: str) -> None:
    for item in ITEMS_TABLE.query(KeyConditionExpression="receipt_id = :r", ExpressionAttributeValues={":r": receipt_id}).get("Items", []):
        ITEMS_TABLE.delete_item(Key={"receipt_id": receipt_id, "line_number": item["line_number"]})
    for payment in PAYMENTS_TABLE.query(KeyConditionExpression="receipt_id = :r", ExpressionAttributeValues={":r": receipt_id}).get("Items", []):
        PAYMENTS_TABLE.delete_item(Key={"receipt_id": receipt_id, "id": payment["id"]})


def update_receipt(receipt_id: str, updates: dict) -> None:
    clean_updates = {key: value for key, value in updates.items() if value is not None}
    if not clean_updates:
        return
    names = {f"#{key}": key for key in clean_updates}
    values = {f":{key}": value for key, value in clean_updates.items()}
    expression = "SET " + ", ".join(f"#{key} = :{key}" for key in clean_updates)
    RECEIPTS_TABLE.update_item(Key={"id": receipt_id}, UpdateExpression=expression, ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def put_log(receipt_id: str, stage: str, status: str, message: str) -> None:
    LOGS_TABLE.put_item(Item={"receipt_id": receipt_id, "created_at": f"{now_iso()}#{uuid.uuid4()}", "stage": stage, "status": status, "message": message})


def records_from_event(event: dict) -> list[dict]:
    if event.get("source") == "aws.s3":
        return [{"bucket": event["detail"]["bucket"]["name"], "key": unquote_plus(event["detail"]["object"]["key"])}]
    records = []
    for record in event.get("Records", []):
        if "s3" in record:
            records.append({"bucket": record["s3"]["bucket"]["name"], "key": unquote_plus(record["s3"]["object"]["key"])})
    return records


def receipt_id_from_key(key: str) -> str | None:
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == "receipts":
        return parts[2]
    return None


def normalize_type(value: str | None) -> str:
    return (value or "").strip().upper().replace(" ", "_").replace("-", "_")


def normalize_store_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def money_string(value) -> str | None:
    amount = decimal_or_none(value)
    if amount is None:
        return None
    return str(amount.quantize(Decimal("0.01")))


def decimal_or_none(value) -> Decimal | None:
    if value is None or value == "":
        return None
    text = str(value).replace("O", "0").replace("o", "0")
    text = re.sub(r"[^0-9,.\-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".") if len(text.split(",")[-1]) == 2 else text.replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def decimal_or_default(value, default: str) -> Decimal:
    return decimal_or_none(value) or Decimal(default)


def normalize_discount(value) -> str | None:
    amount = decimal_or_none(value)
    if amount is None:
        return None
    if amount > 0:
        amount = -amount
    return str(amount.quantize(Decimal("0.01")))


def normalize_payment(value: str | None) -> str | None:
    if not value:
        return None
    upper = value.upper()
    if "TRANSFER" in upper or "BANK" in upper:
        return "TRANSFER"
    if "CASH" in upper:
        return "CASH"
    return upper.replace("MASTER CARD", "MASTERCARD")


def normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(?P<a>\d{1,2})[-/.](?P<b>\d{1,2})[-/.](?P<y>\d{2,4})\b", value)
    if not match:
        return value
    year = int(match.group("y"))
    if year < 100:
        year += 2000
    first = int(match.group("a"))
    second = int(match.group("b"))
    month, day = (second, first) if first > 12 else (first, second)
    return f"{year:04d}-{month:02d}-{day:02d}"


def digits_only(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def object_hash_from_head(head: dict) -> str | None:
    etag = (head.get("ETag") or "").strip('"')
    return etag or None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
