"""Business rules from the spec. Pure functions, no UI — keep them unit-tested."""

from decimal import ROUND_HALF_UP, Decimal

from .errors import ManualReview
from .models import Debtor, Item, OrderData

CENT = Decimal("0.01")
TOLERANCE = Decimal("0.02")

# Spec 2.10.4: extracted payment method -> Fakturama payment code
PAYMENT_CODES = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def norm(s: str) -> str:
    return " ".join(str(s).split()).casefold()


def parse_number(text: str) -> Decimal:
    """'$1,234.50' / '19%' / '19.00 %' -> Decimal. Assumes '.' decimals, as Fakturama displays here."""
    cleaned = "".join(ch for ch in str(text) if ch.isdigit() or ch in ".-")
    if not cleaned:
        raise ManualReview("parse", f"not a number: {text!r}")
    return Decimal(cleaned)


def payment_code(method: str) -> str:
    try:
        return PAYMENT_CODES[norm(method)]
    except KeyError:
        raise ManualReview("payment", f"no payment-code mapping for {method!r}")


def vat_name(pct: Decimal) -> str:
    """Spec 3.5: VAT rows are named 'VAT <pct>%', e.g. 'VAT 19%'."""
    return f"VAT {pct.normalize():f}%"


def gross_price(item: Item) -> Decimal:
    """Spec 3.9: Product master price, without the line discount."""
    return money(item.unit_net * (1 + item.vat_pct / 100))


def line_net(item: Item) -> Decimal:
    """Spec 3.16: qty x unit net x (1 - discount/100)."""
    return money(item.quantity * item.unit_net * (1 - item.discount_pct / 100))


def check_order(order: OrderData) -> None:
    """Arithmetic consistency of the extracted data. Raises ManualReview on mismatch."""
    problems = []
    if not order.items:
        problems.append("no items")
    for i, it in enumerate(order.items, 1):
        if abs(line_net(it) - it.total) > TOLERANCE:
            problems.append(f"item {i} ({it.sku}): computed {line_net(it)} != source {it.total}")
    net = sum((line_net(it) for it in order.items), Decimal(0))
    vat = sum((line_net(it) * it.vat_pct / 100 for it in order.items), Decimal(0))
    if abs(net - order.total_net) > TOLERANCE:
        problems.append(f"total net: computed {net} != source {order.total_net}")
    if abs(money(vat) - order.total_vat) > TOLERANCE:
        problems.append(f"total VAT: computed {money(vat)} != source {order.total_vat}")
    if abs(order.total_net + order.total_vat - order.total) > TOLERANCE:
        problems.append(f"net + VAT != total {order.total}")
    if order.payment.paid and order.payment.date is None:
        problems.append("status PAID but no payment date")
    if problems:
        raise ManualReview("validate", "; ".join(problems))


def cell_eq(cell: str, expected: str) -> bool:
    """Grid cell vs expected value. Narrow columns truncate with '...', which then
    counts as a match only if the visible part is a prefix of the expected value."""
    c, e = norm(cell).replace(" ", ""), norm(expected).replace(" ", "")  # OCR may drop or add spaces
    for ellipsis in ("...", "…"):
        if c.endswith(ellipsis):
            head = c[: -len(ellipsis)]
            return bool(head) and e.startswith(head)
    return c == e


def debtor_matches(row: dict[str, str], d: Debtor) -> bool:
    """Spec 2.3: exact match on Company, First Name, Name, ZIP and City."""
    expected = {
        "Company": d.company,
        "First Name": d.first_name,
        "Name": d.last_name,
        "ZIP": d.billing.zip,
        "City": d.billing.city,
    }
    return all(cell_eq(row.get(col, ""), val) for col, val in expected.items())


def pick_exact(rows: list, matches, step: str):
    """0 matches -> None (create branch), 1 -> that row, >1 -> manual review."""
    hits = [r for r in rows if matches(r)]
    if len(hits) > 1:
        raise ManualReview(step, f"{len(hits)} exact matches, ambiguous")
    return hits[0] if hits else None
