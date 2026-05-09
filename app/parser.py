from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from statistics import mean
from typing import Any

from .config import DEFAULT_CURRENCY
from .utils import money_to_decimal, parse_decimal, titleish


DATE_PATTERNS = [
    re.compile(r"\b(?P<y>20\d{2}|19\d{2})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})\b"),
    re.compile(r"\b(?P<a>\d{1,2})[-/.](?P<b>\d{1,2})[-/.](?P<y>\d{2,4})\b"),
]
TIME_PATTERN = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>AM|PM|A\.M\.|P\.M\.)?\b", re.I)
MONEY_PATTERN = re.compile(r"(?<!\w)([-(]?\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+|[oO])(?:[.,]\d{2})\)?)(?!\w)")
PRICE_TOKEN = r"(?:\d+(?:[.,]\d+)?|[.,]\d+)"
WEIGHT_PATTERN = re.compile(
    rf"(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>LB|LBS|KG|G|OZ|EA|CT)\s*@\s*\$?(?P<unit_price>{PRICE_TOKEN})(?:\s*/\s*(?:LB|LBS|KG|G|OZ|EA|CT))?",
    re.I,
)
MULTIPLIER_PATTERN = re.compile(rf"(?P<qty>\d+(?:[.,]\d+)?)\s*(?:@|X|x|FOR)\s*\$?(?P<unit_price>{PRICE_TOKEN})", re.I)
CARD_LAST4_PATTERN = re.compile(r"(?:\*{2,}|X{2,}|ending|last\s*4|card)\s*(?P<last4>\d{4})", re.I)

TOTAL_LABELS = {
    "subtotal": re.compile(r"\b(SUB\s*TOTAL|SUBTOTAL|MERCHANDISE TOTAL|ITEM TOTAL)\b", re.I),
    "tax": re.compile(r"\b(SALES TAX|STATE TAX|LOCAL TAX|VAT|GST|HST|TAX)\b", re.I),
    "discount": re.compile(r"\b(DISCOUNT|SAVINGS|COUPON|LOYALTY|PROMO|CARD PRICE)\b", re.I),
    "fee": re.compile(r"\b(BAG FEE|BOTTLE DEP|CRV|SERVICE CHARGE|DELIVERY FEE|FEE)\b", re.I),
    "tip": re.compile(r"\b(TIP|GRATUITY)\b", re.I),
    "total": re.compile(r"\b(GRAND TOTAL|AMOUNT DUE|BALANCE DUE|TOTAL)\b", re.I),
}
PAYMENT_LABEL = re.compile(r"\b(CASH|CREDIT|DEBIT|VISA|MASTERCARD|MASTER CARD|AMEX|DISCOVER|EBT|SNAP|TRANSFER|BANK TRANSFER|CHANGE)\b", re.I)
ITEM_STOP = re.compile(r"\b(SUB\s*TOTAL|SUBTOTAL|TAX|TOTAL|AMOUNT DUE|BALANCE|PAYMENT|CHANGE|CASH|CREDIT|DEBIT|VISA|MASTERCARD|THANK YOU)\b", re.I)
NOISE_LINE = re.compile(r"\b(THANK YOU|CUSTOMER COPY|RECEIPT|TERMINAL|AUTH|APPROVED|REF #|AID:|TVR:|TSI:)\b", re.I)


def parse_receipt_text(text: str, currency_code: str = DEFAULT_CURRENCY) -> dict[str, Any]:
    lines = normalize_lines(text)
    store_name = extract_store_name(lines)
    store_address = extract_store_address(lines, store_name)
    store_phone = extract_phone(lines)
    receipt_date = extract_date(lines)
    receipt_time = extract_time(lines)
    totals = extract_totals(lines)
    items, discounts = extract_items(lines)
    if discounts:
        totals["discount"] = sum((money_to_decimal(discount.get("amount")) or Decimal("0.00")) for discount in discounts)
    payments = extract_payments(lines, totals.get("total"))
    taxes = []
    if totals.get("tax") is not None:
        taxes.append({"label": "Tax", "tax_amount": totals["tax"], "confidence": 0.91, "metadata": {}})

    confidence_parts = []
    if store_name:
        confidence_parts.append(0.9)
    if receipt_date:
        confidence_parts.append(0.92)
    if totals.get("total") is not None:
        confidence_parts.append(0.95)
    confidence_parts.extend(item["confidence"] for item in items)
    overall_confidence = round(mean(confidence_parts), 4) if confidence_parts else 0.0

    return {
        "store_raw_name": store_name,
        "store_address": store_address,
        "store_phone": store_phone,
        "store_confidence": 0.9 if store_name else 0.0,
        "receipt_date": receipt_date.isoformat() if receipt_date else None,
        "receipt_time": receipt_time.isoformat() if receipt_time else None,
        "currency_code": currency_code,
        "transaction_id": extract_labeled_value(lines, ("INVOICE_RECEIPT_ID", "TICKET", "TRANS", "TRX", "TXN", "ORDER", "INVOICE")),
        "register_id": extract_labeled_value(lines, ("REG", "REGISTER", "LANE", "TERM")),
        "cashier_name": extract_labeled_value(lines, ("CASHIER", "CLERK", "OPERATOR")),
        "customer_name": extract_labeled_text(lines, "CUSTOMER"),
        "seller": extract_labeled_text(lines, "SELLER"),
        "subtotal_amount": decimal_to_wire(totals.get("subtotal")),
        "tax_amount": decimal_to_wire(totals.get("tax")),
        "discount_amount": decimal_to_wire(totals.get("discount")),
        "fee_amount": decimal_to_wire(totals.get("fee")),
        "tip_amount": decimal_to_wire(totals.get("tip")),
        "total_amount": decimal_to_wire(totals.get("total")),
        "items": items,
        "taxes": taxes,
        "discounts": discounts,
        "payments": payments,
        "parser_metadata": {
            "line_count": len(lines),
            "raw_totals": {key: decimal_to_wire(value) for key, value in totals.items()},
        },
    }


def normalize_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw.replace("\t", " ")).strip()
        if line:
            lines.append(line)
    return lines


def extract_store_name(lines: list[str]) -> str | None:
    for label in ("MERCHANT_NAME", "VENDOR_NAME", "SUPPLIER_NAME"):
        value = extract_labeled_text(lines, label)
        if value:
            return titleish(value)
    for line in lines[:8]:
        if len(line) < 2:
            continue
        if MONEY_PATTERN.search(line) or DATE_PATTERNS[0].search(line) or DATE_PATTERNS[1].search(line):
            continue
        if PAYMENT_LABEL.search(line) or NOISE_LINE.search(line):
            continue
        if re.search(r"\d{3}[-.\s]\d{3}[-.\s]\d{4}", line):
            continue
        return titleish(line)
    return None


def extract_store_address(lines: list[str], store_name: str | None) -> str | None:
    for label in ("MERCHANT_ADDRESS", "VENDOR_ADDRESS", "SUPPLIER_ADDRESS"):
        value = extract_labeled_text(lines, label)
        if value:
            return value
    address_lines = []
    store_seen = False
    for line in lines[:8]:
        if store_name and line.strip().lower() == store_name.strip().lower():
            store_seen = True
            continue
        if not store_seen and store_name:
            continue
        if re.search(r"\b(TEL|PHONE|DATE|TICKET|CUSTOMER|SELLER)\b", line, re.I):
            break
        if MONEY_PATTERN.search(line):
            break
        if len(line) >= 4:
            address_lines.append(line)
    return ", ".join(address_lines) or None


def extract_phone(lines: list[str]) -> str | None:
    for label in ("MERCHANT_PHONE", "VENDOR_PHONE", "SUPPLIER_PHONE"):
        value = extract_labeled_text(lines, label)
        if value:
            return re.sub(r"\D+", "", value)
    for line in lines[:10]:
        match = re.search(r"(?:TEL|PHONE|MOBILE)?\s*:?\s*(\+?\d[\d\s().-]{6,}\d)", line, re.I)
        if match:
            return re.sub(r"\D+", "", match.group(1))
    return None


def extract_date(lines: list[str]) -> date | None:
    for line in lines:
        for pattern in DATE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            groups = match.groupdict()
            try:
                if "a" in groups and groups.get("a"):
                    first = int(groups["a"])
                    second = int(groups["b"])
                    year = normalize_year(int(groups["y"]))
                    if first > 12:
                        month, day = second, first
                    else:
                        month, day = first, second
                else:
                    year = int(groups["y"])
                    month = int(groups["m"])
                    day = int(groups["d"])
                parsed = date(year, month, day)
                if date(1990, 1, 1) <= parsed <= date(2100, 1, 1):
                    return parsed
            except ValueError:
                continue
    return None


def normalize_year(year: int) -> int:
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year


def extract_time(lines: list[str]) -> time | None:
    for line in lines:
        match = TIME_PATTERN.search(line)
        if not match:
            continue
        hour = int(match.group("h"))
        minute = int(match.group("m"))
        second = int(match.group("s") or 0)
        ampm = (match.group("ampm") or "").upper().replace(".", "")
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        try:
            return time(hour, minute, second)
        except ValueError:
            continue
    return None


def extract_totals(lines: list[str]) -> dict[str, Decimal | None]:
    totals: dict[str, Decimal | None] = {"subtotal": None, "tax": None, "discount": None, "fee": None, "tip": None, "total": None}
    for line in lines:
        amounts = money_values(line)
        if not amounts:
            continue
        for field, pattern in TOTAL_LABELS.items():
            if not pattern.search(line):
                continue
            amount = amounts[-1]
            if field == "discount" and amount > 0:
                amount = -amount
            if field == "total" and TOTAL_LABELS["subtotal"].search(line):
                continue
            if field == "tax" and TOTAL_LABELS["subtotal"].search(line):
                continue
            totals[field] = amount
    return totals


def extract_items(lines: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    discounts: list[dict[str, Any]] = []
    in_items = False
    pending_name: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        upper = line.upper()

        if TOTAL_LABELS["subtotal"].search(line):
            in_items = False
        if not in_items and looks_like_item_line(line):
            in_items = True

        if not in_items:
            continue
        if NOISE_LINE.search(line):
            continue
        if ITEM_STOP.search(line):
            continue

        amounts = money_values(line)
        if not amounts:
            if len(line) > 3 and not PAYMENT_LABEL.search(line):
                pending_name = f"{pending_name} {line}".strip() if pending_name else line
            continue

        if is_discount_line(line):
            amount = amounts[-1]
            if amount > 0:
                amount = -amount
            discounts.append(
                {
                    "label": clean_label(strip_last_money(line)),
                    "discount_type": "receipt_or_item",
                    "amount": decimal_to_wire(amount),
                    "confidence": 0.86,
                    "metadata": {"raw_text": line},
                }
            )
            continue

        item = parse_item_line(line, amounts[-1], len(items) + 1, pending_name)
        pending_name = None
        if item:
            items.append(item)

    return items, discounts


def looks_like_item_line(line: str) -> bool:
    if ITEM_STOP.search(line) or PAYMENT_LABEL.search(line):
        return False
    amounts = money_values(line)
    if not amounts:
        return False
    label_hit = any(pattern.search(line) for pattern in TOTAL_LABELS.values())
    return not label_hit


def parse_item_line(line: str, total: Decimal, line_number: int, pending_name: str | None) -> dict[str, Any] | None:
    description = strip_last_money(line)
    if pending_name:
        description = f"{pending_name} {description}".strip()
    description = re.sub(r"\b[FTNAB]\s*$", "", description, flags=re.I).strip()

    quantity = Decimal("1.000")
    unit = "ea"
    unit_price: Decimal | None = None
    confidence = 0.88

    weight = WEIGHT_PATTERN.search(description)
    if weight:
        quantity = parse_decimal(weight.group("qty")) or quantity
        unit = normalize_unit(weight.group("unit"))
        unit_price = money_to_decimal(weight.group("unit_price"))
        description = (description[: weight.start()] + description[weight.end() :]).strip()
        confidence = 0.93
    else:
        multiplier = MULTIPLIER_PATTERN.search(description)
        if multiplier:
            quantity = parse_decimal(multiplier.group("qty")) or quantity
            unit_price = money_to_decimal(multiplier.group("unit_price"))
            description = (description[: multiplier.start()] + description[multiplier.end() :]).strip()
            confidence = 0.91

    if unit_price is None and quantity and quantity != Decimal("0"):
        if quantity != Decimal("1.000"):
            unit_price = (total / quantity).quantize(Decimal("0.0001"))
        else:
            unit_price = total.quantize(Decimal("0.0001"))

    item_name = clean_item_name(description)
    if not item_name or len(item_name) < 2:
        return None
    if re.fullmatch(r"\d+", item_name):
        return None

    if not unit_price:
        confidence -= 0.08
    if total < 0:
        confidence -= 0.03

    return {
        "line_number": line_number,
        "page_number": 1,
        "raw_text": line,
        "item_name_raw": description,
        "item_name_clean": item_name,
        "quantity": str(quantity),
        "unit": unit,
        "unit_price_amount": decimal_to_wire(unit_price, "0.0001"),
        "total_price_amount": decimal_to_wire(total),
        "discount_amount": "0.00",
        "tax_amount": "0.00",
        "is_taxable": detect_taxable_flag(line),
        "is_discount": False,
        "is_return": total < 0,
        "confidence": round(max(0.0, min(0.99, confidence)), 4),
        "review_required": confidence < 0.8,
        "bbox": None,
        "parser_notes": {},
    }


def extract_payments(lines: list[str], total: Decimal | None) -> list[dict[str, Any]]:
    payments = []
    change_amount = None
    for line in lines:
        if re.search(r"\bCHANGE\b", line, re.I):
            amounts = money_values(line)
            if amounts:
                change_amount = amounts[-1]
            continue
        if not PAYMENT_LABEL.search(line):
            continue
        amounts = money_values(line)
        method_match = PAYMENT_LABEL.search(line)
        last4_match = CARD_LAST4_PATTERN.search(line)
        payments.append(
            {
                "method": method_match.group(1).upper().replace("MASTER CARD", "MASTERCARD") if method_match else None,
                "card_brand": method_match.group(1).upper() if method_match and method_match.group(1).upper() in {"VISA", "MASTERCARD", "AMEX", "DISCOVER"} else None,
                "card_last4": last4_match.group("last4") if last4_match else None,
                "amount": decimal_to_wire(amounts[-1] if amounts else total),
                "change_amount": decimal_to_wire(change_amount),
                "authorization_code": extract_auth_code(line),
                "confidence": 0.83,
                "metadata": {"raw_text": redact_card_numbers(line)},
            }
        )
    if change_amount is not None and payments:
        payments[-1]["change_amount"] = decimal_to_wire(change_amount)
    return payments


def extract_labeled_value(lines: list[str], labels: tuple[str, ...]) -> str | None:
    label_regex = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"\b(?:{label_regex})\b\s*[:#-]?\s*([A-Za-z0-9-]+)", re.I)
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(1)
    return None


def extract_labeled_text(lines: list[str], label: str) -> str | None:
    pattern = re.compile(rf"\b{re.escape(label)}\b\s*[:#-]?\s*(.+)$", re.I)
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(1).strip()
    return None


def extract_auth_code(line: str) -> str | None:
    match = re.search(r"\bAUTH\s*[:#-]?\s*([A-Za-z0-9-]+)", line, re.I)
    return match.group(1) if match else None


def money_values(line: str) -> list[Decimal]:
    values = []
    for match in MONEY_PATTERN.finditer(line):
        amount = money_to_decimal(match.group(1))
        if amount is not None:
            values.append(amount)
    return values


def strip_last_money(line: str) -> str:
    matches = list(MONEY_PATTERN.finditer(line))
    if not matches:
        return line.strip()
    match = matches[-1]
    return (line[: match.start()] + line[match.end() :]).strip(" -:\t")


def is_discount_line(line: str) -> bool:
    return bool(TOTAL_LABELS["discount"].search(line) or re.search(r"\b(CPN|MFR|SAVINGS)\b", line, re.I))


def clean_label(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(":- ")
    return titleish(value) or "Discount"


def clean_item_name(value: str) -> str:
    value = re.sub(r"\b(?:UPC|SKU|PLU)\s*[:#]?\s*\d+\b", "", value, flags=re.I)
    value = re.sub(r"^\d{4,14}\s+", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = value.strip(" -:;")
    return titleish(value) or value


def normalize_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    unit = unit.lower()
    if unit == "lbs":
        return "lb"
    return unit


def detect_taxable_flag(line: str) -> bool | None:
    if re.search(r"\sT\s*(?:\$?\d|$)", line):
        return True
    if re.search(r"\sN\s*(?:\$?\d|$)", line):
        return False
    return None


def decimal_to_wire(value: Decimal | None, places: str = "0.01") -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal(places)))


def redact_card_numbers(line: str) -> str:
    return re.sub(r"\b\d{12,19}\b", "[redacted-card-number]", line)
