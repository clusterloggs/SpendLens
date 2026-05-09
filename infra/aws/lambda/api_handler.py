from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import unquote

import boto3


dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

BUCKET = os.environ["RECEIPT_BUCKET"]
STORES_TABLE = dynamodb.Table(os.environ["STORES_TABLE"])
RECEIPTS_TABLE = dynamodb.Table(os.environ["RECEIPTS_TABLE"])
ITEMS_TABLE = dynamodb.Table(os.environ["RECEIPT_ITEMS_TABLE"])
LOGS_TABLE = dynamodb.Table(os.environ["PROCESSING_LOGS_TABLE"])
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "NGN")
PRESIGNED_URL_SECONDS = int(os.getenv("PRESIGNED_URL_SECONDS", "600"))


def handler(event, _context):
    try:
        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        path = event.get("rawPath", "/")
        user_id = authenticated_user_id(event)

        if method == "POST" and path == "/receipts/uploads":
            return json_response(create_upload_session(user_id, parse_body(event)))
        if method == "GET" and path == "/receipts":
            return json_response(list_receipts(user_id))

        receipt_match = re.fullmatch(r"/receipts/([^/]+)", path)
        if receipt_match and method == "GET":
            return json_response(get_receipt(user_id, receipt_match.group(1)))
        if receipt_match and method == "PATCH":
            return json_response(update_receipt(user_id, receipt_match.group(1), parse_body(event)))

        approve_match = re.fullmatch(r"/receipts/([^/]+)/approve", path)
        if approve_match and method == "POST":
            return json_response(approve_receipt(user_id, approve_match.group(1)))

        return json_response({"detail": "Not found"}, 404)
    except PermissionError as exc:
        return json_response({"detail": str(exc)}, 403)
    except ValueError as exc:
        return json_response({"detail": str(exc)}, 400)
    except Exception as exc:
        return json_response({"detail": str(exc)}, 500)


def create_upload_session(user_id: str, payload: dict) -> dict:
    file_name = sanitize_filename(payload.get("file_name") or "receipt")
    content_type = payload.get("content_type") or "image/jpeg"
    receipt_id = str(uuid.uuid4())
    created_at = now_iso()
    key = f"receipts/{user_id}/{receipt_id}/{file_name}"

    RECEIPTS_TABLE.put_item(
        Item={
            "id": receipt_id,
            "user_id": user_id,
            "status": "awaiting_upload",
            "original_file_name": file_name,
            "image_path": key,
            "content_type": content_type,
            "currency_code": payload.get("currency_code") or DEFAULT_CURRENCY,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    put_log(receipt_id, "upload", "created", "Upload session created.")

    upload_url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={"Bucket": BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=PRESIGNED_URL_SECONDS,
    )

    return {
        "receipt_id": receipt_id,
        "upload_url": upload_url,
        "method": "PUT",
        "s3_key": key,
        "expires_in_seconds": PRESIGNED_URL_SECONDS,
        "required_headers": {"Content-Type": content_type},
    }


def list_receipts(user_id: str) -> dict:
    response = RECEIPTS_TABLE.query(
        IndexName="user-created-index",
        KeyConditionExpression="user_id = :user_id",
        ExpressionAttributeValues={":user_id": user_id},
        ScanIndexForward=False,
        Limit=50,
    )
    return {"receipts": [public_receipt(item) for item in response.get("Items", [])]}


def get_receipt(user_id: str, receipt_id: str) -> dict:
    receipt = require_receipt_owner(user_id, receipt_id)
    items = ITEMS_TABLE.query(
        KeyConditionExpression="receipt_id = :receipt_id",
        ExpressionAttributeValues={":receipt_id": receipt_id},
        ScanIndexForward=True,
    ).get("Items", [])
    logs = LOGS_TABLE.query(
        KeyConditionExpression="receipt_id = :receipt_id",
        ExpressionAttributeValues={":receipt_id": receipt_id},
        ScanIndexForward=False,
        Limit=20,
    ).get("Items", [])

    return {
        **public_receipt(receipt),
        "items": [decimal_to_json(item) for item in items],
        "events": [decimal_to_json(log) for log in logs],
    }


def update_receipt(user_id: str, receipt_id: str, payload: dict) -> dict:
    require_receipt_owner(user_id, receipt_id)
    allowed = {
        "ticket_number",
        "receipt_date",
        "receipt_time",
        "customer_name",
        "seller",
        "currency_code",
        "total_amount",
    }
    updates = {key: value for key, value in payload.items() if key in allowed}
    if not updates:
        return get_receipt(user_id, receipt_id)
    updates["updated_at"] = now_iso()
    updates["status"] = "needs_review"

    expression_names = {f"#{key}": key for key in updates}
    expression_values = {f":{key}": to_dynamo_value(value) for key, value in updates.items()}
    update_expression = "SET " + ", ".join(f"#{key} = :{key}" for key in updates)

    RECEIPTS_TABLE.update_item(
        Key={"id": receipt_id},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )
    put_log(receipt_id, "review", "corrected", "Receipt header updated.")
    return get_receipt(user_id, receipt_id)


def approve_receipt(user_id: str, receipt_id: str) -> dict:
    require_receipt_owner(user_id, receipt_id)
    RECEIPTS_TABLE.update_item(
        Key={"id": receipt_id},
        UpdateExpression="SET #status = :status, approved_at = :approved_at, updated_at = :updated_at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": "approved", ":approved_at": now_iso(), ":updated_at": now_iso()},
    )
    put_log(receipt_id, "review", "approved", "Receipt approved.")
    return get_receipt(user_id, receipt_id)


def require_receipt_owner(user_id: str, receipt_id: str) -> dict:
    receipt = RECEIPTS_TABLE.get_item(Key={"id": receipt_id}).get("Item")
    if not receipt:
        raise ValueError("Receipt not found.")
    if receipt.get("user_id") != user_id:
        raise PermissionError("You do not have access to this receipt.")
    return receipt


def put_log(receipt_id: str, stage: str, status: str, message: str) -> None:
    created_at = f"{now_iso()}#{uuid.uuid4()}"
    LOGS_TABLE.put_item(Item={"receipt_id": receipt_id, "created_at": created_at, "stage": stage, "status": status, "message": message})


def authenticated_user_id(event) -> str:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    user_id = claims.get("sub")
    if not user_id:
        raise PermissionError("Missing authenticated user.")
    return user_id


def parse_body(event) -> dict:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return json.loads(body)


def json_response(payload: dict, status_code: int = 200) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(decimal_to_json(payload)),
    }


def public_receipt(receipt: dict) -> dict:
    return decimal_to_json(receipt)


def decimal_to_json(value):
    if isinstance(value, list):
        return [decimal_to_json(item) for item in value]
    if isinstance(value, dict):
        return {key: decimal_to_json(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


def to_dynamo_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def sanitize_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return name[:160] or "receipt"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
