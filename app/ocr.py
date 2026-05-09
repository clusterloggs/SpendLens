from __future__ import annotations

import shutil
import subprocess
import time
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import OCR_PROVIDER
from .parser import extract_date, extract_time
from .utils import money_to_decimal, parse_decimal


class OCRUnavailable(RuntimeError):
    pass


@dataclass
class OCRResult:
    provider: str
    text: str
    confidence: float | None = None
    page_count: int = 1
    model_name: str | None = None
    model_version: str | None = None
    processing_ms: int | None = None
    metadata: dict = field(default_factory=dict)


def run_ocr(file_path: Path, content_type: str | None, manual_text: str | None = None) -> OCRResult:
    if manual_text and manual_text.strip():
        return OCRResult(
            provider="manual_text",
            text=manual_text.strip(),
            confidence=0.99,
            model_name="user_supplied_text",
            metadata={"source": "process_request.ocr_text"},
        )

    suffix = file_path.suffix.lower()
    if content_type == "text/plain" or suffix == ".txt":
        text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            raise OCRUnavailable("Uploaded text file is empty.")
        return OCRResult(
            provider="text_file",
            text=text,
            confidence=0.99,
            model_name="plain_text_passthrough",
            metadata={"source": "uploaded_text_file"},
        )

    if OCR_PROVIDER in {"aws_textract", "textract"}:
        return run_aws_textract_analyze_expense(file_path)

    tesseract_path = shutil.which("tesseract")
    if tesseract_path and suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
        return run_tesseract_cli(tesseract_path, file_path)

    raise OCRUnavailable(
        "No local OCR engine is configured. Upload a .txt receipt, provide OCR text in the review box, "
        "install Tesseract CLI, or connect a cloud OCR provider."
    )


def run_aws_textract_analyze_expense(file_path: Path) -> OCRResult:
    try:
        import boto3
    except ImportError as exc:
        raise OCRUnavailable("AWS Textract OCR selected, but boto3 is not installed. Run: pip install boto3") from exc

    started = time.perf_counter()
    client = boto3.client("textract")
    response = client.analyze_expense(Document={"Bytes": file_path.read_bytes()})
    elapsed = int((time.perf_counter() - started) * 1000)
    text = textract_expense_to_text(response)
    extraction = textract_response_to_extraction(response)
    if not text:
        raise OCRUnavailable("AWS Textract returned no extractable receipt text.")
    return OCRResult(
        provider="aws_textract_analyze_expense",
        text=text,
        confidence=None,
        model_name="AnalyzeExpense",
        processing_ms=elapsed,
        metadata={
            "expense_document_count": len(response.get("ExpenseDocuments", [])),
            "structured_extraction": extraction,
        },
    )


def textract_response_to_extraction(response: dict[str, Any]) -> dict[str, Any]:
    summary_values: dict[str, dict[str, Any]] = {}
    other_values: list[str] = []
    items: list[dict[str, Any]] = []
    detected_currency = None
    full_text = textract_expense_to_text(response)

    for document in response.get("ExpenseDocuments", []):
        for field in document.get("SummaryFields", []):
            field_type = normalize_textract_type(field.get("Type", {}).get("Text"))
            label = field.get("LabelDetection", {}).get("Text") or ""
            value = field.get("ValueDetection", {}).get("Text")
            confidence = field.get("ValueDetection", {}).get("Confidence") or field.get("Confidence")
            currency = extract_currency_code(field)
            if currency:
                detected_currency = currency
            if not value:
                continue
            if field_type == "OTHER":
                other_values.append(value)
            summary_values[field_type] = {
                "value": value,
                "label": label,
                "confidence": confidence_to_unit(confidence),
                "currency": currency,
            }

            label_upper = label.upper()
            if "CUSTOMER" in label_upper and not summary_values.get("CUSTOMER_NAME"):
                summary_values["CUSTOMER_NAME"] = {"value": value, "label": label, "confidence": confidence_to_unit(confidence)}
            if "SELLER" in label_upper or "CASHIER" in label_upper:
                summary_values["SELLER"] = {"value": value, "label": label, "confidence": confidence_to_unit(confidence)}

        infer_customer_seller_from_other_values(summary_values, other_values)

        for group in document.get("LineItemGroups", []):
            for line_item in group.get("LineItems", []):
                mapped_item = textract_line_item_to_extraction(line_item, len(items) + 1)
                if mapped_item:
                    items.append(mapped_item)

    raw_date = summary_text(summary_values, "INVOICE_RECEIPT_DATE") or summary_text(summary_values, "RECEIPT_DATE")
    raw_time = summary_text(summary_values, "PURCHASE_TIME") or summary_text(summary_values, "TRANSACTION_TIME")

    return {
        "store_raw_name": summary_text(summary_values, "VENDOR_NAME") or summary_text(summary_values, "SUPPLIER_NAME"),
        "store_address": summary_text(summary_values, "VENDOR_ADDRESS") or summary_text(summary_values, "SUPPLIER_ADDRESS"),
        "store_phone": digits_only(summary_text(summary_values, "VENDOR_PHONE") or summary_text(summary_values, "SUPPLIER_PHONE")),
        "store_confidence": summary_confidence(summary_values, "VENDOR_NAME") or summary_confidence(summary_values, "SUPPLIER_NAME") or 0.0,
        "receipt_date": normalize_date_text(raw_date),
        "receipt_time": normalize_time_text(raw_time),
        "currency_code": detected_currency or infer_currency_from_text(full_text),
        "transaction_id": summary_text(summary_values, "INVOICE_RECEIPT_ID") or summary_text(summary_values, "RECEIPT_ID") or extract_ticket_number(full_text),
        "register_id": summary_text(summary_values, "REGISTER_ID"),
        "cashier_name": summary_text(summary_values, "CASHIER"),
        "customer_name": summary_text(summary_values, "CUSTOMER_NAME"),
        "seller": summary_text(summary_values, "SELLER"),
        "subtotal_amount": summary_amount(summary_values, "SUBTOTAL"),
        "total_amount": summary_amount(summary_values, "TOTAL") or summary_amount(summary_values, "AMOUNT_DUE"),
        "items": items,
        "parser_metadata": {
            "source": "aws_textract_analyze_expense",
            "summary_field_count": len(summary_values),
            "line_item_count": len(items),
        },
    }


def textract_line_item_to_extraction(line_item: dict[str, Any], line_number: int) -> dict[str, Any] | None:
    fields: dict[str, dict[str, Any]] = {}
    for field in line_item.get("LineItemExpenseFields", []):
        field_type = normalize_textract_type(field.get("Type", {}).get("Text"))
        value = field.get("ValueDetection", {}).get("Text")
        confidence = field.get("ValueDetection", {}).get("Confidence") or field.get("Confidence")
        if field_type and value:
            fields[field_type] = {"value": value, "confidence": confidence_to_unit(confidence)}

    item_name = (
        text_from(fields, "ITEM")
        or text_from(fields, "DESCRIPTION")
        or text_from(fields, "PRODUCT_CODE")
        or text_from(fields, "OTHER")
    )
    if not item_name:
        return None

    quantity = parse_decimal(text_from(fields, "QUANTITY")) or Decimal("1.000")
    unit_price = amount_from(fields, "UNIT_PRICE")
    total_price = amount_from(fields, "PRICE") or amount_from(fields, "AMOUNT")

    if unit_price is None and total_price is not None and quantity:
        try:
            unit_price = (Decimal(total_price) / quantity).quantize(Decimal("0.01"))
        except (InvalidOperation, ZeroDivisionError):
            unit_price = None

    confidence_values = [value["confidence"] for value in fields.values() if value.get("confidence") is not None]
    confidence = round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.85

    return {
        "line_number": line_number,
        "page_number": 1,
        "raw_text": " ".join(value["value"] for value in fields.values() if value.get("value")),
        "item_name_raw": item_name,
        "item_name_clean": item_name,
        "quantity": str(quantity),
        "unit": "ea",
        "unit_price_amount": unit_price,
        "total_price_amount": total_price,
        "is_return": False,
        "confidence": confidence,
        "review_required": confidence < 0.8,
        "bbox": None,
        "parser_notes": {"source": "aws_textract_line_item"},
    }


def textract_expense_to_text(response: dict) -> str:
    lines: list[str] = []
    for document in response.get("ExpenseDocuments", []):
        for block in document.get("Blocks", []):
            if block.get("BlockType") == "LINE" and block.get("Text"):
                lines.append(block["Text"])

        for field in document.get("SummaryFields", []):
            field_type = field.get("Type", {}).get("Text")
            value = field.get("ValueDetection", {}).get("Text")
            if field_type and value:
                lines.append(f"{field_type}: {value}")

        for group in document.get("LineItemGroups", []):
            for line_item in group.get("LineItems", []):
                by_type = {}
                for field in line_item.get("LineItemExpenseFields", []):
                    field_type = field.get("Type", {}).get("Text")
                    value = field.get("ValueDetection", {}).get("Text")
                    if field_type and value:
                        by_type[field_type.upper()] = value
                description = by_type.get("ITEM") or by_type.get("DESCRIPTION") or by_type.get("PRODUCT_CODE")
                quantity = by_type.get("QUANTITY")
                unit_price = by_type.get("UNIT_PRICE")
                price = by_type.get("PRICE") or by_type.get("AMOUNT")
                row = " ".join(part for part in [description, quantity, unit_price, price] if part)
                if row:
                    lines.append(row)
    return "\n".join(lines)


def infer_customer_seller_from_other_values(summary_values: dict[str, dict[str, Any]], values: list[str]) -> None:
    if summary_values.get("CUSTOMER_NAME") and summary_values.get("SELLER"):
        return
    for value in values:
        clean = value.strip()
        if not clean:
            continue
        lower = clean.lower()
        if "@" in clean and not summary_values.get("SELLER"):
            summary_values["SELLER"] = {"value": clean, "label": "OTHER", "confidence": 0.7}
        elif "customer" in lower and not summary_values.get("CUSTOMER_NAME"):
            summary_values["CUSTOMER_NAME"] = {"value": clean, "label": "OTHER", "confidence": 0.7}


def infer_currency_from_text(text: str) -> str | None:
    if "₦" in text:
        return "NGN"
    if re.search(r"(?<![A-Z])N\s?\d+(?:[,.]\d{2})", text, re.I):
        return "NGN"
    if re.search(r"(?<![A-Z])#\s?\d+(?:[,.]\d{2})", text):
        return "NGN"
    return None


def extract_ticket_number(text: str) -> str | None:
    patterns = [
        r"\bTicket\s*[:#-]?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9 _./:-]{4,})",
        r"\bReceipt\s*(?:No|Number|#)?\s*[:#-]?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9 _./:-]{4,})",
        r"\b(?P<value>[A-Z]+(?:\s+[A-Z0-9]+)*CASHPOINT\d*[-A-Z0-9]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group("value")).strip(" .")
    return None


def normalize_textract_type(value: str | None) -> str:
    return (value or "").strip().upper().replace(" ", "_").replace("-", "_")


def extract_currency_code(field: dict[str, Any]) -> str | None:
    for key in ("ValueDetection", "Type"):
        code = field.get(key, {}).get("Currency", {}).get("Code")
        if code and code != "UNKNOWN":
            return code.upper()
    return None


def confidence_to_unit(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 1:
        numeric = numeric / 100
    return round(max(0.0, min(1.0, numeric)), 4)


def summary_text(summary_values: dict[str, dict[str, Any]], field: str) -> str | None:
    value = summary_values.get(field, {}).get("value")
    return str(value).strip() if value else None


def summary_amount(summary_values: dict[str, dict[str, Any]], field: str) -> str | None:
    return decimal_to_wire(money_to_decimal(summary_text(summary_values, field)))


def summary_confidence(summary_values: dict[str, dict[str, Any]], field: str) -> float | None:
    return summary_values.get(field, {}).get("confidence")


def text_from(fields: dict[str, dict[str, Any]], field: str) -> str | None:
    value = fields.get(field, {}).get("value")
    return str(value).strip() if value else None


def amount_from(fields: dict[str, dict[str, Any]], field: str) -> str | None:
    return decimal_to_wire(money_to_decimal(text_from(fields, field)))


def decimal_to_wire(value: Decimal | None, places: str = "0.01") -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal(places)))


def digits_only(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def normalize_date_text(value: str | None) -> str | None:
    if not value:
        return None
    parsed = extract_date([value])
    return parsed.isoformat() if parsed else value


def normalize_time_text(value: str | None) -> str | None:
    if not value:
        return None
    parsed = extract_time([value])
    return parsed.isoformat() if parsed else value


def run_tesseract_cli(tesseract_path: str, file_path: Path) -> OCRResult:
    started = time.perf_counter()
    command = [tesseract_path, str(file_path), "stdout", "--psm", "6"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    elapsed = int((time.perf_counter() - started) * 1000)
    if completed.returncode != 0:
        raise OCRUnavailable(completed.stderr.strip() or "Tesseract failed.")
    text = completed.stdout.strip()
    if not text:
        raise OCRUnavailable("Tesseract returned no text.")
    return OCRResult(
        provider="tesseract_cli",
        text=text,
        confidence=None,
        model_name="tesseract",
        processing_ms=elapsed,
        metadata={"command": "tesseract stdout --psm 6"},
    )
