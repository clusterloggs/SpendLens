from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    receipts = relationship("Receipt", back_populates="store")


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    store_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("stores.id"), index=True)

    ticket_number: Mapped[str | None] = mapped_column(Text, index=True)
    original_file_name: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    image_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    content_type: Mapped[str | None] = mapped_column(Text)

    receipt_date: Mapped[object | None] = mapped_column(Date, index=True)
    receipt_time: Mapped[object | None] = mapped_column(Time)
    customer_name: Mapped[str | None] = mapped_column(Text)
    seller: Mapped[str | None] = mapped_column(Text)

    currency_code: Mapped[str] = mapped_column(String(3), default="USD")
    subtotal_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    tax_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    discount_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    total_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))

    raw_ocr_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    validation_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    store = relationship("Store", back_populates="receipts")
    items = relationship("ReceiptItem", cascade="all, delete-orphan", back_populates="receipt", order_by="ReceiptItem.line_number")
    payments = relationship("ReceiptPayment", cascade="all, delete-orphan", back_populates="receipt")
    logs = relationship("ProcessingLog", cascade="all, delete-orphan", back_populates="receipt", order_by="ProcessingLog.created_at")


class ReceiptItem(Base):
    __tablename__ = "receipt_items"
    __table_args__ = (UniqueConstraint("receipt_id", "line_number", name="uq_receipt_item_line"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id"), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    item_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    total_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    receipt = relationship("Receipt", back_populates="items")


class ReceiptPayment(Base):
    __tablename__ = "receipt_payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id"), index=True)
    method: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    change_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    receipt = relationship("Receipt", back_populates="payments")


class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id"), index=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    receipt = relationship("Receipt", back_populates="logs")
