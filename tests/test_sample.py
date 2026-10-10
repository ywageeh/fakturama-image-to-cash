"""The sample order from the task PDF (samples/order.png), transcribed by hand."""

import json
from decimal import Decimal as D
from pathlib import Path

from i2c.models import OrderData
from i2c.rules import check_order, gross_price, payment_code

EXPECTED = OrderData.model_validate(json.loads((Path(__file__).parent / "fixtures/order_expected.json").read_text()))


def test_sample_is_consistent():
    check_order(EXPECTED)


def test_sample_master_data_values():
    chair, mat = EXPECTED.items
    assert gross_price(chair) == D("297.50")  # 250.00 * 1.19, discount not applied
    assert gross_price(mat) == D("47.60")
    assert payment_code(EXPECTED.payment.method) == "Credit transfer"
