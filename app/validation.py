from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from statistics import mean
from typing import Any


def validate_extraction(extraction: dict[str, Any]) -> tuple[str, list[dict[str, str]], float]:
    errors: list[dict[str, str]] = []

    items = extraction.get("items", [])
    if not items:
        errors.append(
            {
                "code": "NO_ITEMS_FOUND",
                "message": "No receipt line items were parsed from the OCR text.",
                "severity": "error",
            }
        )

    if not extraction.get("store_raw_name"):
        errors.append({"code": "MISSING_STORE", "message": "Store name could not be extracted.", "severity": "warning"})

    receipt_date = extraction.get("receipt_date")
    if not receipt_date:
        errors.append({"code": "MISSING_DATE", "message": "Receipt date could not be extracted.", "severity": "warning"})
    else:
        try:
            parsed_date = date.fromisoformat(receipt_date)
            if parsed_date > date.today():
                errors.append({"code": "FUTURE_DATE", "message": "Receipt date appears to be in the future.", "severity": "warning"})
        except ValueError:
            errors.append({"code": "INVALID_DATE", "message": "Receipt date is not a valid ISO date.", "severity": "error"})

    total = dec(extraction.get("total_amount"))
    subtotal = dec(extraction.get("subtotal_amount"))
    tax = dec(extraction.get("tax_amount")) or Decimal("0.00")
    discount = dec(extraction.get("discount_amount")) or Decimal("0.00")
    fee = dec(extraction.get("fee_amount")) or Decimal("0.00")
    tip = dec(extraction.get("tip_amount")) or Decimal("0.00")

    if total is None:
        errors.append({"code": "MISSING_TOTAL", "message": "Receipt total could not be extracted.", "severity": "warning"})

    item_sum = sum((dec(item.get("total_price_amount")) or Decimal("0.00")) for item in items)
    if subtotal is not None and items:
        difference = abs(item_sum - subtotal)
        if difference > Decimal("0.05"):
            errors.append(
                {
                    "code": "ITEM_SUBTOTAL_MISMATCH",
                    "message": f"Item sum {item_sum} differs from subtotal {subtotal} by {difference}.",
                    "severity": "warning",
                }
            )

    if subtotal is not None and total is not None:
        expected = subtotal + tax + fee + tip + discount
        difference = abs(expected - total)
        if difference > Decimal("0.05"):
            errors.append(
                {
                    "code": "TOTAL_RECONCILIATION_WARNING",
                    "message": f"Subtotal, tax, fee, tip, and discounts produce {expected}, not total {total}.",
                    "severity": "warning",
                }
            )

    low_conf_items = [item for item in items if float(item.get("confidence") or 0) < 0.8]
    if low_conf_items:
        errors.append(
            {
                "code": "LOW_CONFIDENCE_ITEMS",
                "message": f"{len(low_conf_items)} item row(s) require review.",
                "severity": "warning",
            }
        )

    confidence_values = []
    if extraction.get("store_raw_name"):
        confidence_values.append(float(extraction.get("store_confidence") or 0.0))
    if extraction.get("receipt_date"):
        confidence_values.append(0.92)
    if total is not None:
        confidence_values.append(0.95)
    confidence_values.extend(float(item.get("confidence") or 0.0) for item in items)
    overall_confidence = round(mean(confidence_values), 4) if confidence_values else 0.0

    if any(error["severity"] == "error" for error in errors):
        status = "parse_failed"
    elif overall_confidence >= 0.88 and not any(error["severity"] == "warning" for error in errors):
        status = "validated"
    else:
        status = "needs_review"

    return status, errors, overall_confidence


def dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
