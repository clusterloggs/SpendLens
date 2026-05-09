import unittest
from pathlib import Path

from app.parser import parse_receipt_text
from app.validation import validate_extraction


SAMPLE = Path("samples/sample-receipt.txt").read_text(encoding="utf-8")


class ReceiptParserTests(unittest.TestCase):
    def test_extracts_header_totals_and_items(self):
        extraction = parse_receipt_text(SAMPLE)

        self.assertEqual(extraction["store_raw_name"], "Fresh Valley Market #204")
        self.assertEqual(extraction["receipt_date"], "2026-05-05")
        self.assertEqual(extraction["receipt_time"], "18:23:00")
        self.assertEqual(extraction["subtotal_amount"], "23.26")
        self.assertEqual(extraction["total_amount"], "22.45")
        self.assertEqual(len(extraction["items"]), 6)
        self.assertNotIn("payments", extraction)

        bananas = extraction["items"][0]
        self.assertEqual(bananas["item_name_clean"], "Bananas")
        self.assertEqual(bananas["quantity"], "1.245")
        self.assertEqual(bananas["unit"], "lb")
        self.assertEqual(bananas["unit_price_amount"], "0.6900")
        self.assertEqual(bananas["total_price_amount"], "0.86")

    def test_validation_accepts_reconciled_sample(self):
        extraction = parse_receipt_text(SAMPLE)
        status, errors, confidence = validate_extraction(extraction)

        self.assertEqual(status, "validated")
        self.assertEqual(errors, [])
        self.assertGreaterEqual(confidence, 0.88)


if __name__ == "__main__":
    unittest.main()
