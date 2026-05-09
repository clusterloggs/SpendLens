import os
import tempfile
import unittest
from pathlib import Path


TMP_DIR = Path(tempfile.mkdtemp(prefix="receipt-scanner-test-"))
os.environ["RECEIPT_APP_DATA_DIR"] = str(TMP_DIR)
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DIR / 'test.db'}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


SAMPLE = Path("samples/sample-receipt.txt").read_text(encoding="utf-8")


class ApiFlowTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_upload_process_and_fetch_receipt(self):
        session_response = self.client.post(
            "/api/receipts/uploads",
            json={
                "file_name": "sample-receipt.txt",
                "content_type": "text/plain",
                "file_size_bytes": len(SAMPLE.encode("utf-8")),
                "currency_code": "USD",
            },
        )
        self.assertEqual(session_response.status_code, 200, session_response.text)
        session = session_response.json()

        upload_response = self.client.put(
            session["upload_url"],
            content=SAMPLE.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(upload_response.status_code, 200, upload_response.text)

        process_response = self.client.post(f"/api/receipts/{session['receipt_id']}/process", json={})
        self.assertEqual(process_response.status_code, 200, process_response.text)

        detail_response = self.client.get(f"/api/receipts/{session['receipt_id']}")
        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        receipt = detail_response.json()

        self.assertEqual(receipt["status"], "validated")
        self.assertEqual(receipt["store_raw_name"], "Fresh Valley Market #204")
        self.assertEqual(receipt["total_amount"], 22.45)
        self.assertEqual(len(receipt["items"]), 6)
        self.assertEqual(receipt["payments"][0]["method"], "VISA")
        self.assertEqual(receipt["payments"][0]["amount"], 22.45)


if __name__ == "__main__":
    unittest.main()
