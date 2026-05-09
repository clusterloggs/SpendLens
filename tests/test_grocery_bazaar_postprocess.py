import unittest

from app.parser import parse_receipt_text
from app.postprocess import polish_extraction
from app.validation import validate_extraction


OCR_TEXT = """GROCERY BAZAAR LIMITED
71, Meiran Road
Near Kabowei Junction, Meiran
Tel: 08150891199
Date: 5/8/2026 3:14:31 PM
Ticket MEIRAN CASHPOINT1-26-05-08-055
Customer: Walk-in Customer
Seller: victoria.oamen@grocerybazaai.store
Item
Qty
Price
Total
GB Medium Carrier Bag
1
N100.00
₦100.00
Golden Bite Sardine
1
₦1500.00
₦1500.00
Bread
Total Paid: A1600.00
transfer #1600.00
Change 40.00
Thank you, please visit again
ITEMS BOUGHT IN GOOD CONDITION
SHOULD NO HE RETURNED IN A HAD FORM
ADDRESS: GROCERY BAZAAR LIMITED
71, Meiran Road
Near Kabowei Junction, Meiran
STREET: 71, Meiran Road
STREET: Near Kabowei Junction,
CITY: Meiran
NAME: GROCERY BAZAAR LIMITED
ADDRESS_BLOCK: 71, Meiran Road
Near Kabowei Junction, Meiran
AMOUNT_PAID: #1600.00
INVOICE_RECEIPT_DATE: 5/8/2026
TOTAL: A1600.00
VENDOR_ADDRESS: GROCERY BAZAAR LIMITED
71, Meiran Road
Near Kabowei Junction, Meiran
VENDOR_NAME: GROCERY BAZAAR LIMITED
VENDOR_PHONE: 08150891199
OTHER: Walk-in Customer
OTHER: victoria.oamen@grocerybazaai.store
OTHER: 40.00
GB Medium Carrier Bag 1 N100.00 ₦100.00
Golden Bite Sardine
Bread 1 ₦1500.00 ₦1500.00"""


class GroceryBazaarPostprocessTests(unittest.TestCase):
    def test_polishes_realistic_grocery_bazaar_ocr(self):
        extraction = parse_receipt_text(OCR_TEXT, "NGN")
        extraction = polish_extraction(extraction, OCR_TEXT)

        self.assertEqual(extraction["currency_code"], "NGN")
        self.assertEqual(extraction["transaction_id"], "MEIRAN CASHPOINT1-26-05-08-055")
        self.assertEqual(extraction["receipt_date"], "2026-05-08")
        self.assertEqual(extraction["receipt_time"], "15:14:31")
        self.assertEqual(extraction["customer_name"], "Walk-in Customer")
        self.assertEqual(extraction["seller"], "victoria.oamen@grocerybazaai.store")
        self.assertEqual(extraction["total_amount"], "1600.00")

        names = [item["item_name_clean"] for item in extraction["items"]]
        self.assertIn("GB Medium Carrier Bag", names)
        self.assertIn("Golden Bite Sardine Bread", names)

        self.assertNotIn("payments", extraction)

        status, errors, _confidence = validate_extraction(extraction)
        self.assertEqual(status, "validated")
        self.assertEqual(errors, [])
        self.assertFalse(any(error["code"] == "CHANGE_AMOUNT_REPAIRED" for error in errors))


if __name__ == "__main__":
    unittest.main()
