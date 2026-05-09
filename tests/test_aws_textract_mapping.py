import unittest

from app.ocr import textract_response_to_extraction
from app.validation import validate_extraction


class AwsTextractMappingTests(unittest.TestCase):
    def test_maps_analyze_expense_response_to_mvp_extraction(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        summary("VENDOR_NAME", "GROCERY BAZAAR LIMITED", 98),
                        summary("VENDOR_ADDRESS", "71, Meiran Road, Near Kabowei Junction, Meiran", 96),
                        summary("VENDOR_PHONE", "08150891199", 97),
                        summary("INVOICE_RECEIPT_DATE", "5/8/2026", 95),
                        summary("INVOICE_RECEIPT_ID", "MEIRAN CASHPOINT1-26-05-08-055", 94),
                        summary("SUBTOTAL", "₦1600.00", 97, "NGN"),
                        summary("TOTAL", "₦1600.00", 98, "NGN"),
                        summary("AMOUNT_PAID", "₦1600.00", 98, "NGN"),
                        summary("PAYMENT_TYPE", "Transfer", 90),
                        summary("OTHER", "Walk-in Customer", 88, label="Customer"),
                        summary("OTHER", "victoria.oamau@grocerybazaar.store", 88, label="Seller"),
                    ],
                    "LineItemGroups": [
                        {
                            "LineItems": [
                                line_item("GB Medium Carrier Bag", "1", "₦100.00", "₦100.00"),
                                line_item("Golden Bite Sardine Bread", "1", "₦1500.00", "₦1500.00"),
                            ]
                        }
                    ],
                }
            ]
        }

        extraction = textract_response_to_extraction(response)

        self.assertEqual(extraction["store_raw_name"], "GROCERY BAZAAR LIMITED")
        self.assertEqual(extraction["store_phone"], "08150891199")
        self.assertEqual(extraction["receipt_date"], "2026-05-08")
        self.assertEqual(extraction["currency_code"], "NGN")
        self.assertEqual(extraction["transaction_id"], "MEIRAN CASHPOINT1-26-05-08-055")
        self.assertEqual(extraction["customer_name"], "Walk-in Customer")
        self.assertEqual(extraction["seller"], "victoria.oamau@grocerybazaar.store")
        self.assertEqual(extraction["total_amount"], "1600.00")
        self.assertEqual(len(extraction["items"]), 2)
        self.assertEqual(extraction["items"][1]["item_name_clean"], "Golden Bite Sardine Bread")
        self.assertNotIn("payments", extraction)

        status, errors, confidence = validate_extraction(extraction)
        self.assertEqual(status, "validated", errors)
        self.assertGreaterEqual(confidence, 0.88)


def summary(field_type, value, confidence, currency=None, label=None):
    value_detection = {"Text": value, "Confidence": confidence}
    if currency:
        value_detection["Currency"] = {"Code": currency, "Confidence": confidence}
    return {
        "Type": {"Text": field_type, "Confidence": confidence},
        "LabelDetection": {"Text": label or field_type},
        "ValueDetection": value_detection,
    }


def line_item(name, quantity, unit_price, price):
    return {
        "LineItemExpenseFields": [
            item_field("ITEM", name, 98),
            item_field("QUANTITY", quantity, 96),
            item_field("UNIT_PRICE", unit_price, 95),
            item_field("PRICE", price, 97),
        ]
    }


def item_field(field_type, value, confidence):
    return {
        "Type": {"Text": field_type, "Confidence": confidence},
        "ValueDetection": {"Text": value, "Confidence": confidence},
    }


if __name__ == "__main__":
    unittest.main()
