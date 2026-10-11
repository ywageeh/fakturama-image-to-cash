from datetime import date
from decimal import Decimal as D

import pytest

from i2c.errors import ManualReview
from i2c.models import Address, Debtor, Item, OrderData, Payment
from i2c.rules import cell_eq, check_order, debtor_matches, parse_number, gross_price, line_net, payment_code, pick_exact, vat_name


def item(**kw):
    base = dict(sku="A-1", description="Widget", quantity=D(2), unit_net=D("10.00"),
                vat_pct=D(19), discount_pct=D(10), total=D("18.00"))
    return Item(**{**base, **kw})


def order(**kw):
    base = dict(
        order_date=date(2026, 3, 5), external_ref="PO-1",
        debtor=Debtor(company="ACME GmbH", first_name="Jane", last_name="Doe",
                      billing=Address(street="Main St 1", zip="10115", city="Berlin")),
        payment=Payment(method="Bank Transfer", paid=True, date=date(2026, 3, 6)),
        items=[item()], total_net=D("18.00"), total_vat=D("3.42"), total=D("21.42"),
    )
    return OrderData(**{**base, **kw})


def test_line_and_gross_price():
    assert line_net(item()) == D("18.00")
    assert gross_price(item(unit_net=D("12.34"))) == D("14.68")  # 12.34 * 1.19 = 14.6846


def test_vat_name():
    assert vat_name(D("19")) == "VAT 19%"
    assert vat_name(D("7.0")) == "VAT 7%"
    assert vat_name(D("5.5")) == "VAT 5.5%"


def test_payment_code():
    assert payment_code(" bank  TRANSFER ") == "Credit transfer"
    with pytest.raises(ManualReview):
        payment_code("Cash")


def test_check_order_ok():
    check_order(order())


@pytest.mark.parametrize("kw", [
    {"total": D("99.00")},
    {"items": [item(total=D("20.00"))]},
    {"payment": Payment(method="Bank Transfer", paid=True)},
    {"items": []},
])
def test_check_order_flags_problems(kw):
    with pytest.raises(ManualReview):
        check_order(order(**kw))


def test_debtor_match_is_exact_on_five_columns():
    d = order().debtor
    row = {"Company": "ACME  GmbH", "First Name": "jane", "Name": "Doe", "ZIP": "10115", "City": "Berlin"}
    assert debtor_matches(row, d)
    assert not debtor_matches({**row, "ZIP": "10117"}, d)


def test_pick_exact():
    assert pick_exact([1, 2, 3], lambda x: x == 2, "s") == 2
    assert pick_exact([1, 3], lambda x: x == 2, "s") is None
    with pytest.raises(ManualReview):
        pick_exact([2, 2], lambda x: x == 2, "s")


def test_cell_eq_truncated():
    assert cell_eq("Northstar Office ...", "Northstar Office GmbH")
    assert not cell_eq("Northstar Other ...", "Northstar Office GmbH")
    assert not cell_eq("...", "anything")
    assert cell_eq("Berlin", " berlin ")


@pytest.mark.parametrize("shown, value", [
    ("EUR678.30", "678.30"), ("EUR1,234.57", "1234.57"), ("EUR-1,234.57", "-1234.57"),
    ("€1,234.57", "1234.57"), ("$297.50", "297.50"), ("-10.00 %", "-10.00"), ("19%", "19"),
])
def test_parse_number_ignores_currency_and_thousands(shown, value):
    assert parse_number(shown) == D(value)


def test_check_order_with_order_discount_and_shipping():
    # lines 18.00; 5% order discount -> goods 17.10; shipping 10.00 net; VAT 19% on both
    kw = dict(order_discount_pct=D(5), shipping_net=D("10.00"), total_vat=D("5.15"), total=D("32.25"))
    check_order(order(total_net=D("27.10"), **kw))  # printed net total includes shipping
    check_order(order(total_net=D("17.10"), **kw))  # or excludes it
    with pytest.raises(ManualReview):
        check_order(order(total_net=D("18.00"), **kw))  # discount not reflected in the net total
    with pytest.raises(ManualReview):
        check_order(order(total_net=D("27.10"), **{**kw, "total": D("22.25")}))  # shipping missing from the total


def test_check_order_rejects_unexplained_extra_charge():
    # a shipping charge on the document that was not extracted makes the totals inconsistent
    with pytest.raises(ManualReview):
        check_order(order(total=D("31.42")))
