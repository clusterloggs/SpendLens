from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str | None, fallback: str = "receipt") -> str:
    base = Path(name or fallback).name.strip() or fallback
    cleaned = FILENAME_SAFE_RE.sub("_", base)
    return cleaned[:160] or fallback


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalize_store_name(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"[^A-Za-z0-9]+", " ", value).strip().lower()
    text = re.sub(r"\b(store|market|location|loc|no|number|#)\s*\d+\b", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def titleish(value: str | None) -> str | None:
    if not value:
        return value
    upper_ratio = sum(1 for ch in value if ch.isupper()) / max(1, sum(1 for ch in value if ch.isalpha()))
    if upper_ratio > 0.75:
        return value.title()
    return value.strip()


def money_to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = str(value).strip()
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = text.replace("O", "0").replace("o", "0")
    text = re.sub(r"[^0-9,.\-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text and "." not in text:
        parts = text.split(",")
        if len(parts[-1]) == 2:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = text.replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if negative and amount > 0:
        amount = -amount
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def decimal_to_str(value: Any, places: str = "0.01") -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def parse_decimal(value: Any, places: str = "0.001") -> Decimal | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal(places), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_iso_time(value: Any) -> time | None:
    if not value:
        return None
    if isinstance(value, time):
        return value
    text = str(value)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text[:8], fmt).time()
        except ValueError:
            continue
    return None


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def decimalish(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
