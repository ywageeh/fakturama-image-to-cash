"""Order image -> validated OrderData, using the vision LLM."""

from pathlib import Path

from pydantic import ValidationError

from .errors import ManualReview
from .llm import LLM
from .models import OrderData
from .rules import check_order

SYSTEM = """You extract purchase-order data from an image. Reply with one JSON object only.
Copy values exactly as printed; never guess. Use "" for missing text, null for a missing date.
Numbers: plain decimals with a dot, no currency symbol or code, no thousands separators.
Read the document's own convention first: "1.234,50" (European) and "1,234.50" (English)
both mean 1234.50, and "EUR 570.00" means 570.00.
Dates: YYYY-MM-DD. Percentages: the number only (19 for 19%).

Schema:
{
  "order_date": "YYYY-MM-DD",
  "external_ref": "the customer's PO / reference number",
  "debtor": {
    "company": "", "first_name": "", "last_name": "", "alias": "", "email": "", "phone": "",
    "billing":  {"name": "", "street": "", "zip": "", "city": "", "country": ""},
    "delivery": {"name": "", "street": "", "zip": "", "city": "", "country": ""} or null if same as billing
  },
  (split a single contact name into first_name / last_name; "name" is the line above the street),
  "payment": {"method": "e.g. Bank Transfer", "paid": true/false (true only if status is PAID), "date": "YYYY-MM-DD" or null},
  "items": [{"sku": "", "description": "", "quantity": 0, "unit_net": 0, "vat_pct": 0,
             "discount_pct": 0, "total": "line net total after discount as printed"}],
  "total_net": 0, "total_vat": 0, "total": 0
}"""


def extract(image: Path, llm: LLM, attempts: int = 2) -> OrderData:
    """Ask the LLM, validate shape and arithmetic; retry once with the errors as feedback."""
    prompt = "Extract the order as JSON."
    error = ""
    for _ in range(attempts):
        try:
            order = OrderData.model_validate(llm.json(SYSTEM, prompt, image))
            check_order(order)
            return order
        except (ValidationError, ManualReview, ValueError) as e:
            error = str(e)
            prompt = f"Extract the order as JSON. Your previous answer failed these checks, re-read the image carefully:\n{error}"
    raise ManualReview("extract", error)
