from __future__ import annotations

import re
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from .parser import extract_date, extract_time, normalize_lines
from .utils import money_to_decimal, parse_decimal


def polish_extraction(extraction: dict[str, Any], raw_text: str) -> dict[str, Any]:
    """Repair common OCR/receipt issues after provider extraction."""
    lines = normalize_lines(raw_text)
    notes: list[dict[str, str]] = []

    apply_header_fallbacks(extraction, lines)
    apply_currency_fallback(extraction, raw_text)
    apply_total_fallbacks(extraction, lines)
    merge_split_items(extraction)
    clean_filter_and_dedupe_items(extraction)
    flag_suspicious_email(extraction, notes)

    metadata = extraction.setdefault("parser_metadata", {})
    metadata["postprocess_notes"] = notes
    return extraction


def apply_header_fallbacks(extraction: dict[str, Any], lines: list[str]) -> None:
    text = "\n".join(lines)
    ticket = extract_ticket_number(text)
    current_ticket = extraction.get("transaction_id")
    if ticket and (not current_ticket or len(ticket) > len(str(current_ticket))):
        extraction["transaction_id"] = ticket

    if not extraction.get("receipt_date"):
        parsed_date = extract_date(lines)
        if parsed_date:
            extraction["receipt_date"] = parsed_date.isoformat()

    if not extraction.get("receipt_time"):
        parsed_time = extract_time(lines)
        if parsed_time:
            extraction["receipt_time"] = parsed_time.isoformat()

    if not extraction.get("customer_name"):
        value = extract_labeled_text(lines, "Customer")
        if value:
            extraction["customer_name"] = value

    if not extraction.get("seller"):
        value = extract_labeled_text(lines, "Seller")
        if value:
            extraction["seller"] = value


def apply_currency_fallback(extraction: dict[str, Any], raw_text: str) -> None:
    if extraction.get("currency_code") and extraction["currency_code"] != "USD":
        return
    if "₦" in raw_text or re.search(r"(?<![A-Z])N\s?\d+(?:[,.]\d{2})", raw_text, re.I) or re.search(r"(?<![A-Z])#\s?\d+(?:[,.]\d{2})", raw_text):
        extraction["currency_code"] = "NGN"


def apply_total_fallbacks(extraction: dict[str, Any], lines: list[str]) -> None:
    if not extraction.get("total_amount"):
        total = extract_labeled_amount(lines, ("TOTAL", "TOTAL PAID"))
        if total:
            extraction["total_amount"] = total
    if not extraction.get("subtotal_amount"):
        subtotal = extract_labeled_amount(lines, ("SUBTOTAL", "SUB TOTAL"))
        if subtotal:
            extraction["subtotal_amount"] = subtotal


def merge_split_items(extraction: dict[str, Any]) -> None:
    items = extraction.get("items", [])
    if not items:
        return

    merged: list[dict[str, Any]] = []
    skip_next = False
    for index, item in enumerate(items):
        if skip_next:
            skip_next = False
            continue

        name = item.get("item_name_clean") or ""
        next_item = items[index + 1] if index + 1 < len(items) else None
        if next_item and looks_like_name_continuation(next_item):
            continuation = next_item.get("item_name_clean") or ""
            item = {**item, "item_name_clean": f"{name} {continuation}".strip(), "item_name_raw": f"{item.get('item_name_raw') or name} {continuation}".strip()}
            skip_next = True
        merged.append(item)

    for line_number, item in enumerate(merged, start=1):
        item["line_number"] = line_number
    extraction["items"] = merged


def clean_filter_and_dedupe_items(extraction: dict[str, Any]) -> None:
    cleaned: list[dict[str, Any]] = []
    for item in extraction.get("items", []):
        name = clean_item_name(item.get("item_name_clean") or "")
        raw = item.get("raw_text") or ""
        if not name or is_non_item_name(name, raw):
            continue
        total = money_to_decimal(item.get("total_price_amount"))
        if total is None:
            continue
        item = {**item, "item_name_clean": name, "item_name_raw": clean_item_name(item.get("item_name_raw") or name)}
        maybe_add_deduped_item(cleaned, item)

    for line_number, item in enumerate(cleaned, start=1):
        item["line_number"] = line_number
    extraction["items"] = cleaned


def maybe_add_deduped_item(items: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    candidate_total = money_to_decimal(candidate.get("total_price_amount"))
    candidate_name = candidate.get("item_name_clean") or ""
    for index, existing in enumerate(items):
        existing_total = money_to_decimal(existing.get("total_price_amount"))
        existing_name = existing.get("item_name_clean") or ""
        if candidate_total == existing_total and names_overlap(existing_name, candidate_name):
            if score_item_name(candidate_name) > score_item_name(existing_name):
                items[index] = candidate
            return
    items.append(candidate)


def clean_item_name(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"\s+\d+(?:[.,]\d+)?\s+[N₦#A]?\s*\d+(?:[,.]\d{2}).*$", "", text, flags=re.I)
    text = re.sub(r"\s+[N₦#A]?\s*\d+(?:[,.]\d{2}).*$", "", text, flags=re.I)
    text = re.sub(r"[₦#]\s*$", "", text).strip(" -:")
    return text


def is_non_item_name(name: str, raw: str) -> bool:
    haystack = f"{name} {raw}".upper()
    blocked = (
        "AMOUNT_PAID",
        "TOTAL PAID",
        "TRANSFER",
        "CHANGE",
        "VENDOR_",
        "ADDRESS",
        "ADDRESS_BLOCK",
        "STREET:",
        "CITY:",
        "OTHER:",
        "INVOICE_RECEIPT_DATE",
        "ITEMS BOUGHT",
        "THANK YOU",
    )
    if any(token in haystack for token in blocked):
        return True
    return len(name) > 90


def names_overlap(left: str, right: str) -> bool:
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    return left_norm in right_norm or right_norm in left_norm


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def score_item_name(value: str) -> int:
    return len(normalize_name(value))


def flag_suspicious_email(extraction: dict[str, Any], notes: list[dict[str, str]]) -> None:
    seller = extraction.get("seller")
    store = extraction.get("store_raw_name")
    if not seller or "@" not in seller or not store:
        return
    domain = seller.split("@", 1)[1].lower()
    store_token = re.sub(r"[^a-z0-9]+", "", store.lower())
    domain_token = re.sub(r"[^a-z0-9]+", "", domain.split(".", 1)[0])
    if store_token and domain_token and SequenceMatcher(None, store_token, domain_token).ratio() < 0.68:
        notes.append(
            {
                "code": "SELLER_EMAIL_REVIEW",
                "message": f"Seller email domain '{domain}' may be an OCR typo for the store name.",
            }
        )


def looks_like_name_continuation(item: dict[str, Any]) -> bool:
    return (
        money_to_decimal(item.get("total_price_amount")) in {None, Decimal("0.00")}
        and (parse_decimal(item.get("quantity")) in {None, Decimal("1.000")})
        and bool(item.get("item_name_clean"))
    )


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


def extract_labeled_text(lines: list[str], label: str) -> str | None:
    pattern = re.compile(rf"\b{re.escape(label)}\b\s*[:#-]?\s*(.+)$", re.I)
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(1).strip()
    return None


def extract_labeled_amount(lines: list[str], labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"\b(?:{label_pattern})\b\s*[:#-]?\s*(?P<amount>[A#N₦$]?\s*\d+(?:[,.]\d{{2}})?)", re.I)
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        amount = money_to_decimal(repair_amount_token(match.group("amount")))
        if amount is not None:
            return str(amount)
    return None


def repair_amount_token(value: str) -> str:
    text = value.strip()
    if re.match(r"^[Aa]\s*\d", text):
        return re.sub(r"^[Aa]\s*", "", text)
    return text
