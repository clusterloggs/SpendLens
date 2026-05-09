from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("RECEIPT_APP_DATA_DIR", ROOT_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
ARTIFACT_DIR = DATA_DIR / "artifacts"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'receipts_mvp.db'}")

MAX_IMAGE_UPLOAD_BYTES = int(os.getenv("MAX_IMAGE_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_DOCUMENT_UPLOAD_BYTES = int(os.getenv("MAX_DOCUMENT_UPLOAD_BYTES", str(50 * 1024 * 1024)))
MAX_MANUAL_OCR_CHARS = int(os.getenv("MAX_MANUAL_OCR_CHARS", "200000"))

OCR_PROVIDER = os.getenv("OCR_PROVIDER", "auto").lower()
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "USD")
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

SUPPORTED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
