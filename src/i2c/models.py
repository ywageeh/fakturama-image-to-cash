"""Structured data extracted from the order image."""

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel


class Address(BaseModel):
    name: str = ""  # name line printed above the street, e.g. "Northstar Office Warehouse"
    street: str
    zip: str
    city: str
    country: str = ""


class Debtor(BaseModel):
    company: str = ""
    first_name: str = ""
    last_name: str = ""
    alias: str = ""
    email: str = ""
    phone: str = ""
    billing: Address
    delivery: Address | None = None  # None means same as billing


class Item(BaseModel):
    sku: str
    description: str
    quantity: Decimal
    unit_net: Decimal
    vat_pct: Decimal
    discount_pct: Decimal = Decimal(0)
    total: Decimal  # line total as printed on the source (net, after discount)


class Payment(BaseModel):
    method: str  # e.g. "Bank Transfer", "Credit Card", "SEPA Direct Debit"
    paid: bool
    date: dt.date | None = None


class OrderData(BaseModel):
    order_date: dt.date
    external_ref: str
    debtor: Debtor
    payment: Payment
    items: list[Item]
    order_discount_pct: Decimal = Decimal(0)  # overall discount on the whole order, if the source has one
    shipping_net: Decimal = Decimal(0)  # net shipping charge, if the source has one
    total_net: Decimal
    total_vat: Decimal
    total: Decimal
