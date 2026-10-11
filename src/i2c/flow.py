"""The Order-first flow from the spec. Each step acts, then verifies; anything unexpected
raises ManualReview. Step numbers in comments refer to the take-home spec.
"""

import time
from datetime import date, datetime
from decimal import Decimal

from .errors import ManualReview
from .models import Address, Debtor, Item, OrderData
from .rules import TOLERANCE, cell_eq, debtor_matches, gross_price, items_net, line_net, norm, parse_number, payment_code, pick_exact, vat_name
from .ui.app import Fakturama
from .ui.grid import Row

ORDER_TAB: tuple = ("New Order",)  # + the proposed number (e.g. "PO000001"), the title after saving
INVOICE_TAB: tuple = ("New Invoice",)  # + e.g. "INV000001"
INVOICE_DATE = ""  # the proposed invoice date as displayed (left unchanged, spec 5.1)

FREE_SHIPPING = "Free of shipping costs"


def run(app: Fakturama, order: OrderData) -> None:
    d = order.debtor
    app.step(f"1. Open a New Order: date {order.order_date}, Cust.Ref. {order.external_ref}, Net, With VAT")
    open_order(app, order)

    app.step(f"2.1-2.4 Debtor: search '{d.company}' from the Order")
    if not select_debtor(app, d):
        app.step(f"2.5-2.11 No exact Debtor: resolve payment method '{order.payment.method}', create the Debtor")
        create_debtor(app, order)
        app.step("2.12-2.13 Re-select the new Debtor from the Order, confirm both addresses")
        if not select_debtor(app, d):  # 2.12: re-select proves it was saved
            raise ManualReview("debtor", "new debtor not found after saving")

    for n, item in enumerate(order.items, 1):  # 3.1: in source order
        app.step(f"3.2-3.3 Item {n}: search SKU {item.sku} from the Order")
        if not select_product(app, item):
            app.step(f"3.4-3.11 No exact Product: resolve VAT {item.vat_pct}%, create {item.sku}")
            ensure_vat(app, item.vat_pct)
            create_product(app, item)
            app.step(f"3.12 Re-select {item.sku} from the Order")
            if not select_product(app, item):  # 3.12
                raise ManualReview("product", f"new product {item.sku} not found after saving")
        app.step(f"3.13-3.16 Item {n}: Qty {item.quantity}, Discount {item.discount_pct}%, verify the line")
        fill_line(app, item)

    app.step("4.1-4.3 Check every line, set order-level discount/shipping, check addresses and totals")
    check_order(app, order)
    app.screenshot("order_complete")
    app.step("4.4 Save the Order")
    app.activate_tab(ORDER_TAB)
    app.save()
    app.step("4.5 Verify the Order row in Data > Documents")
    verify_document(app, order, "Orders", ORDER_TAB[1], "open", order.order_date)

    app.step("4.6-4.7 Create the linked Invoice from the Order's follow-up action")
    create_invoice(app)
    app.step("5.1 Check the Invoice against the Order: Cust.Ref., addresses, lines, totals")
    check_invoice(app, order)
    status = f"PAID on {order.payment.date}" if order.payment.paid else "not paid"
    app.step(f"5.2-5.3 Payment: {order.payment.method}, {status}")
    apply_payment(app, order)
    app.screenshot("invoice_complete")
    app.step("5.4 Save the Invoice")
    app.activate_tab(INVOICE_TAB)
    app.save()
    state = "paid" if order.payment.paid else "unpaid"
    app.step(f"5.5 Verify in Data > Documents: Invoice is {state}, Order is still open")
    verify_document(app, order, "Invoices", INVOICE_TAB[1], state, _parse_shown(INVOICE_DATE))
    verify_document(app, order, "Orders", ORDER_TAB[1], "open", order.order_date)
    app.step("5.6 Reopen the Invoice and confirm the stored payment details")
    verify_invoice_persisted(app, order)
    app.step(f"Done: {INVOICE_TAB[1]} {state}, linked to {ORDER_TAB[1]} (open), total {order.total:.2f}")
    app.screenshot("done")


# ---- 1. Order ----------------------------------------------------------------

def open_order(app: Fakturama, order: OrderData) -> None:
    global ORDER_TAB
    app.press("Create: New Order")  # 1.3
    ed = app.editor("New Order")
    ORDER_TAB = ("New Order", app.by_label("No.", parent=ed).iface_value.CurrentValue)  # 1.4: keep the proposed No.
    app.set_date(app.by_label("Date", parent=ed), order.order_date)  # 1.5
    app.set_text(app.find("Cust.Ref.", "Edit", ed), order.external_ref)  # 1.6
    app.set_combo(app.by_label("Date", "ComboBox", ed), "Net")  # 1.7
    app.set_combo(app.find("VAT", "ComboBox", ed), "With VAT")
    app.screenshot("order_opened")


# ---- 2. Debtor ---------------------------------------------------------------

def select_debtor(app: Fakturama, debtor: Debtor) -> bool:
    """2.1-2.4 / 2.12-2.13. True if an exact debtor was selected, False if none exists."""
    app.activate_tab(ORDER_TAB)
    ed = app.editor(ORDER_TAB)
    app.click(app.icons_near("Addresses", ed)[0])  # upper icon = existing contact (NOT the green +)
    dlg = app.dialog("Select the address")
    app.search(dlg, debtor.company or debtor.last_name)
    row = pick_exact(app.rows(dlg), lambda r: debtor_matches(r.cells, debtor), "debtor")
    if row is None:
        app.press("Cancel", dlg)
        return False
    choose(app, dlg, row)
    app.dismiss_info()  # "... has already ordered n times" for a customer with history

    _check_addresses(app, app.editor(ORDER_TAB), debtor, "debtor")  # 2.4 / 2.13
    app.screenshot("debtor_selected")
    return True


def _check_addresses(app: Fakturama, ed, debtor: Debtor, step: str) -> None:
    """The document's Invoice and Delivery address blocks contain the source street, ZIP and city."""
    for tab, a in (("Delivery address", debtor.delivery or debtor.billing), ("Invoice address", debtor.billing)):
        app.focus()
        app.find(tab, "TabItem", ed).click_input()
        app.park_mouse()
        time.sleep(0.3)
        folder = app.find(tab, "Tab", ed)  # the address tab folder is named after its active page
        shown = folder.descendants(control_type="Edit")[0].iface_value.CurrentValue
        missing = [p for p in (a.street, a.zip, a.city) if norm(p) not in norm(shown)]
        if missing:
            raise ManualReview(step, f"{tab} lacks {missing}: {shown!r}")


def create_debtor(app: Fakturama, order: OrderData) -> None:
    """2.5-2.11, leaving the Order tab open.

    Deviation: the payment method is resolved/created BEFORE opening the Debtor editor, because
    the editor's Payment dropdown is filled when it opens and doesn't pick up a method created
    meanwhile (same reason the spec creates the VAT before New product)."""
    d = order.debtor
    method = order.payment.method
    ensure_payment_method(app, method)  # 2.10.1-2.10.6
    app.nav("New Contact")  # 2.5
    app.activate_tab("New Debtor")  # make the new editor the active part (focus may linger in a list view)
    ed = app.editor("New Debtor")
    # 2.6: Customer ID and Salutation stay as proposed
    app.set_text(app.find("Company", "Edit", ed), d.company)
    app.set_text(app.by_label("First Name Last Name", parent=ed, index=0), d.first_name)
    app.set_text(app.by_label("First Name Last Name", parent=ed, index=1), d.last_name)

    # 2.7-2.8: main address = billing; a second address only if delivery differs
    same = d.delivery is None or _same_address(d.billing, d.delivery)
    roles = ["Invoice address", "Delivery address"] if same else ["Invoice address"]
    _fill_address(app, ed, d.billing, d.company, roles, email=d.email, phone=d.phone)
    if not same:
        app.press("+", ed)
        _fill_address(app, ed, d.delivery, d.company, ["Delivery address"])

    # 2.9-2.10: Miscellaneous
    app.find("Miscellaneous", "TabItem", ed).click_input()
    app.set_text(app.find("Alias name", "Edit", ed), d.alias)
    app.set_number(app.find("Discount", "Edit", ed), Decimal(0))
    app.set_combo(app.find("Net or Gross", "ComboBox", ed), "Net")
    app.set_combo(app.find("Payment", "ComboBox", ed), method)  # 2.10

    app.screenshot("debtor_filled")
    app.activate_tab("New Debtor")
    app.save()  # 2.11


def _same_address(a: Address, b: Address) -> bool:
    return all(norm(x) == norm(y) for x, y in ((a.street, b.street), (a.zip, b.zip), (a.city, b.city)))


def _fill_address(app: Fakturama, ed, a: Address, company: str, roles: list[str], email: str = "", phone: str = "") -> None:
    if a.name and norm(a.name) != norm(company):  # only when the source supplies an extra name line
        app.set_text(app.find("additional name", "Edit", ed), a.name)
    app.set_text(app.find("Street", "Edit", ed), a.street)
    app.set_text(app.by_label("ZIP - City", parent=ed, index=0), a.zip)
    app.set_text(app.by_label("ZIP - City", parent=ed, index=1), a.city)
    if a.country:
        app.set_combo(app.find("Country", "ComboBox", ed), a.country)
    if email:
        app.set_text(app.find("E-Mail", "Edit", ed), email)
    if phone:
        app.set_text(app.find("Telephone", "Edit", ed), phone)
    app.click(app.by_label("address type", "Button", ed))  # opens the role popup
    for role in roles:
        app.set_check(app.find(role, "CheckBox"), True)
    app.key("{ESC}")


def ensure_payment_method(app: Fakturama, method: str) -> None:
    """2.10.1-2.10.6: reuse one exact payment method (if its definition matches) or create it."""
    expected_code = payment_code(method)  # no mapping -> manual review before touching the UI
    app.nav("terms of payment")
    view = app.editor("terms of payment")
    app.search(view, method)
    row = pick_exact(app.rows(view), lambda r: cell_eq(r.cells.get("Name", ""), method), "payment")
    if row is not None:  # 2.10.2: reuse only a non-conflicting definition
        ed = _open_row(app, row, method)  # read the definition from its editor: exact, and the code isn't a list column
        problems = []
        description = app.find("Description", "Edit", ed).iface_value.CurrentValue
        if norm(description) != norm(method):
            problems.append(f"Description {description!r}")
        for field in ("Cash discount", "Discount Days", "Net Days"):
            shown = app.find(field, "Edit", ed).iface_value.CurrentValue
            if parse_number(shown) != 0:
                problems.append(f"{field} {shown!r}")
        code = _payment_code_combo(ed).iface_value.CurrentValue.strip()
        if code != expected_code:
            problems.append(f"payment code {code!r}")
        app.close_editor(method)
        if problems:
            raise ManualReview("payment", f"existing {method!r} conflicts with the spec: " + "; ".join(problems))
        return
    app.press("Create a new term of payment", view)
    app.activate_tab("New Term of Payment")  # make the new editor the active part (focus may linger in a list view)
    ed = app.editor("New Term of Payment")
    app.set_text(app.find("Name", "Edit", ed), method)
    app.set_text(app.find("Description", "Edit", ed), method)
    app.set_combo(_payment_code_combo(ed), expected_code)
    app.set_number(app.find("Cash discount", "Edit", ed), Decimal(0))
    app.set_number(app.find("Discount Days", "Edit", ed), Decimal(0))
    app.set_number(app.find("Net Days", "Edit", ed), Decimal(0))
    app.screenshot("payment_method")
    app.save()


def _payment_code_combo(ed):
    """The payment-code dropdown; its label is an untranslated key ('!editorPaymentPaymentcode!')."""
    return next(c for c in ed.descendants(control_type="ComboBox") if "aymentcode" in c.window_text())


def _open_row(app: Fakturama, row: Row, title: str):
    """Open a list row's editor (double-click); the editor tab is titled after the entry."""
    app.click_at(row.x, row.y, double=True)
    ed = app.editor(title)
    app.activate_tab(title)
    return ed


# ---- 3. Products ---------------------------------------------------------------

def select_product(app: Fakturama, item: Item) -> bool:
    """3.2-3.3 / 3.12. True if the exact SKU was added as a new line.

    The selector auto-picks when the search leaves exactly one product, closing itself;
    we then verify the new last line carries our SKU."""
    app.activate_tab(ORDER_TAB)
    ed = app.editor(ORDER_TAB)
    before = len(_item_rows(app, ORDER_TAB))
    app.click(app.icons_near("Items", ed)[0])  # upper icon = select product (NOT the green +)
    dlg = app.dialog("Select a product")
    app.search(dlg, item.sku)
    if not app.is_open("Select a product"):  # auto-picked the single match
        rows = _item_rows(app, ORDER_TAB)
        if len(rows) == before + 1 and cell_eq(rows[-1].cells.get("Item No.", ""), item.sku):
            return True
        raise ManualReview("product", f"selector auto-picked an unexpected product for {item.sku}")
    rows = app.rows(dlg)
    row = pick_exact(rows, lambda r: cell_eq(r.cells.get("Item No.", ""), item.sku), "product")
    if row is None:
        app.press("Cancel", dlg)
        return False
    choose(app, dlg, row)
    return True


def _item_rows(app: Fakturama, tab: tuple) -> list[Row]:
    app.maximize(tab)  # otherwise only ~1 item row is visible
    ed = app.editor(tab)
    return app.rows(app.by_label("Items", "Pane", ed))


def _edit_cell(app: Fakturama, row: Row, column: str, value: Decimal) -> None:
    """Select the cell with one click, then type: NatTable opens its editor on the first
    keystroke (or on the click, if the row is already selected) and Enter commits."""
    app.click_at(row.col_x[column], row.y)
    app.key(f"{value:f}" + "{ENTER}")
    time.sleep(0.5)
    app.key("{ESC}")  # some columns (Qty.) re-open their editor after the commit; close it unchanged
    time.sleep(0.3)


def ensure_vat(app: Fakturama, pct: Decimal) -> None:
    """3.4-3.6: reuse an exact 'VAT n%' row (name, value and code S) or create it."""
    name = vat_name(pct)
    app.nav("VATs")
    view = app.editor("VATs")
    app.search(view, name)
    row = pick_exact(app.rows(view), lambda r: cell_eq(r.cells.get("Name", ""), name), "vat")
    if row is not None:  # 3.5
        ed = _open_row(app, row, name)  # the VAT code isn't a list column
        code = app.find("VAT code (E-Invoice)", "ComboBox", ed).iface_value.CurrentValue
        value = parse_number(app.find("Value", "Edit", ed).iface_value.CurrentValue)
        app.close_editor(name)
        if value != pct or not code.startswith("S "):
            raise ManualReview("vat", f"existing {name!r} conflicts: value {value}, code {code!r}")
        return
    app.press("Create a new tax rate", view)
    app.activate_tab("New TAX Rate")  # make the new editor the active part (focus may linger in a list view)
    ed = app.editor("New TAX Rate")
    app.set_text(app.find("Name", "Edit", ed), name)
    app.set_text(app.find("Description", "Edit", ed), name)
    code = app.find("VAT code (E-Invoice)", "ComboBox", ed).iface_value.CurrentValue
    if not code.startswith("S "):
        raise ManualReview("vat", f"VAT code defaulted to {code!r}, expected S (Standard rate)")
    app.set_number(app.find("Value", "Edit", ed), pct)
    app.screenshot("vat_created")
    app.save()


def create_product(app: Fakturama, item: Item) -> None:
    """3.7-3.11 (the VAT already exists, so it's in the dropdown)."""
    app.nav("New product")
    app.activate_tab("New product")  # make the new editor the active part (focus may linger in a list view)
    ed = app.editor("New product")
    app.set_text(app.find("Item Number", "Edit", ed), item.sku)
    app.set_text(app.find("Name", "Edit", ed), item.description)
    app.set_text(app.find("Description", "Edit", ed), item.description)
    app.set_number(app.by_label("Price (gross)", parent=ed), gross_price(item))
    app.set_number(app.by_label("cost price (net)", parent=ed), Decimal(0))
    app.set_combo(app.find("VAT", "ComboBox", ed), vat_name(item.vat_pct))  # shows the VAT description
    app.set_number(app.find("Stock", "Edit", ed), Decimal(0))
    app.screenshot(f"product_{item.sku}")
    app.save()


def fill_line(app: Fakturama, item: Item) -> None:
    """3.13-3.16 on the line just added (the last one)."""
    def line() -> Row:
        rows = _item_rows(app, ORDER_TAB)
        if not rows or not cell_eq(rows[-1].cells.get("Item No.", ""), item.sku):
            raise ManualReview("line", f"last item line is not {item.sku}")
        return rows[-1]

    _edit_cell(app, line(), "Qty.", item.quantity)  # 3.13
    row = line()
    if parse_number(row.cells.get("U.Price", "")) != item.unit_net:  # 3.14: set or confirm
        _edit_cell(app, row, "U.Price", item.unit_net)
        row = line()
    _edit_cell(app, row, "Discount", item.discount_pct)  # 3.15

    problems = _line_problems(line().cells, item)  # 3.14, 3.16
    if problems:
        raise ManualReview("line", f"{item.sku}: " + "; ".join(problems))
    app.screenshot(f"line_{item.sku}")


def _line_problems(c: dict[str, str], item: Item) -> list[str]:
    """Differences between a document item row and the extracted item."""
    problems = []
    if not cell_eq(c.get("Item No.", ""), item.sku):
        problems.append(f"Item No. {c.get('Item No.')!r}")
    if parse_number(c.get("Qty.", "")) != item.quantity:
        problems.append(f"Qty {c.get('Qty.')!r}")
    if parse_number(c.get("U.Price", "")) != item.unit_net:
        problems.append(f"U.Price {c.get('U.Price')!r}")
    if not _squash(c.get("VAT", "")).startswith(_squash(vat_name(item.vat_pct))):
        problems.append(f"VAT {c.get('VAT')!r}")
    if abs(parse_number(c.get("Discount", ""))) != item.discount_pct:  # shown negative, e.g. "-10.00 %"
        problems.append(f"Discount {c.get('Discount')!r}")
    if abs(parse_number(c.get("Price", "")) - line_net(item)) > TOLERANCE:
        problems.append(f"Price {c.get('Price')!r} != {line_net(item)}")
    return problems


def _check_lines(app: Fakturama, tab: tuple, order: OrderData, step: str) -> None:
    """Every source item appears as a line, in order, with matching values."""
    rows = _item_rows(app, tab)
    if len(rows) != len(order.items):
        raise ManualReview(step, f"{len(rows)} item lines on the document, {len(order.items)} in the source")
    problems = [f"line {i} ({item.sku}): " + "; ".join(p)
                for i, (row, item) in enumerate(zip(rows, order.items), 1)
                if (p := _line_problems(row.cells, item))]
    if problems:
        raise ManualReview(step, " | ".join(problems))


def _check_totals(app: Fakturama, ed, order: OrderData, step: str) -> None:
    """4.2-4.3: overall Discount, Shipping and the totals as Fakturama shows them.
    Fakturama's 'Total Net' is the sum of the lines, before the overall discount and shipping."""
    def number(el) -> Decimal:
        return parse_number(el.iface_value.CurrentValue)

    problems = []
    for label, expected in (("Total Net", items_net(order)), ("VAT", order.total_vat), ("Total", order.total)):
        shown = number(app.find(label, "Edit", ed))
        if abs(shown - expected) > TOLERANCE:
            problems.append(f"{label} shows {shown}, source says {expected}")
    discount = abs(number(app.find("Discount", "Edit", ed)))  # shown negative, e.g. "-5%"
    if discount != order.order_discount_pct:
        problems.append(f"overall Discount shows {discount}%, source says {order.order_discount_pct}%")
    shipping = number(app.by_label("Shipping", "Edit", ed, index=1))
    if shipping != order.shipping_net:
        problems.append(f"Shipping shows {shipping}, source says {order.shipping_net}")
    method = app.find("Shipping", "ComboBox", ed).iface_value.CurrentValue
    if order.shipping_net == 0 and norm(method) != norm(FREE_SHIPPING):
        problems.append(f"Shipping method is {method!r}, expected {FREE_SHIPPING!r}")
    if problems:
        raise ManualReview(step, "; ".join(problems))


def set_order_level(app: Fakturama, ed, order: OrderData, step: str) -> None:
    """4.2: Discount stays 0% and Shipping stays free unless the source supplies order-level
    values. Each field is only touched when it differs from the source."""
    discount = app.find("Discount", "Edit", ed)
    if abs(parse_number(discount.iface_value.CurrentValue)) != order.order_discount_pct:
        shown = app.type_into(discount, f"{order.order_discount_pct:f}")
        if abs(parse_number(shown)) != order.order_discount_pct:
            raise ManualReview(step, f"overall Discount shows {shown!r}, expected {order.order_discount_pct}%")
    shipping = app.by_label("Shipping", "Edit", ed, index=1)
    if parse_number(shipping.iface_value.CurrentValue) != order.shipping_net:
        app.set_number(shipping, order.shipping_net)


# ---- 4. Complete and save the Order ----------------------------------------------

def check_order(app: Fakturama, order: OrderData) -> None:
    """4.1-4.3, right before saving: every line, order-level discount/shipping, addresses, totals."""
    _check_lines(app, ORDER_TAB, order, "order")
    ed = app.editor(ORDER_TAB)
    set_order_level(app, ed, order, "order")
    _check_addresses(app, ed, order.debtor, "order")
    _check_totals(app, ed, order, "order")


def create_invoice(app: Fakturama) -> None:
    """4.6-4.7: the follow-up Invoice button (keeps the Order link) — never the toolbar one."""
    global INVOICE_TAB, INVOICE_DATE
    app.activate_tab(ORDER_TAB)
    ed = app.editor(ORDER_TAB)
    app.press("Invoice", parent=app.find("Create a follow-up document", "Group", ed))
    app.dismiss_info()
    ed = app.editor("New Invoice")
    INVOICE_TAB = ("New Invoice", app.by_label("No.", parent=ed).iface_value.CurrentValue)  # 5.1: keep proposals
    INVOICE_DATE = app.by_label("Date", parent=ed).iface_value.CurrentValue


# ---- 5. Invoice ------------------------------------------------------------------

def check_invoice(app: Fakturama, order: OrderData) -> None:
    """5.1: Cust.Ref., addresses, Order Date, VAT mode, item lines and totals match the Order."""
    _check_lines(app, INVOICE_TAB, order, "invoice")
    ed = app.editor(INVOICE_TAB)
    shown = {
        "Cust.Ref.": app.find("Cust.Ref.", "Edit", ed).iface_value.CurrentValue,
        "VAT": app.find("VAT", "ComboBox", ed).iface_value.CurrentValue,
    }
    expected = {"Cust.Ref.": order.external_ref, "VAT": "With VAT"}
    problems = [f"{k} {shown[k]!r} != {v!r}" for k, v in expected.items() if norm(shown[k]) != norm(v)]
    order_date = app.by_label("Order Date", parent=ed).iface_value.CurrentValue
    if _parse_shown(order_date) != order.order_date:
        problems.append(f"Order Date {order_date!r} != {order.order_date}")
    if problems:
        raise ManualReview("invoice", "; ".join(problems))
    _check_addresses(app, ed, order.debtor, "invoice")
    # Fakturama's follow-up action copies the overall discount but not a shipping amount typed
    # on the Order (it takes the shipping method's own value), so bring the Invoice in line.
    set_order_level(app, ed, order, "invoice")
    _check_totals(app, ed, order, "invoice")


def _payment_controls(app: Fakturama, ed):
    """The 'paid' checkbox and the unnamed payment-method combo to its right."""
    paid = app.find("paid", "CheckBox", ed)
    pr = paid.rectangle()
    method = min(
        (c for c in ed.descendants(control_type="ComboBox")
         if c.rectangle().left > pr.right and c.rectangle().top <= pr.bottom and c.rectangle().bottom >= pr.top),
        key=lambda c: c.rectangle().left, default=None)
    if method is None:
        raise ManualReview("payment", "payment method selector not found on the invoice")
    return paid, method


def apply_payment(app: Fakturama, order: OrderData) -> None:
    """5.2-5.3: payment method must match; if PAID tick paid, set date and the full total."""
    ed = app.editor(INVOICE_TAB)
    paid, method = _payment_controls(app, ed)
    if norm(method.iface_value.CurrentValue) != norm(order.payment.method):
        app.set_combo(method, order.payment.method)  # ManualReview if the method isn't available

    if not order.payment.paid:  # leave paid clear; don't invent a date or value
        app.set_check(paid, False)
        return
    app.set_check(paid, True)
    app.set_date(app.by_label("at", parent=ed), order.payment.date)
    total = parse_number(app.find("Total", "Edit", ed).iface_value.CurrentValue)
    app.set_number(app.find("Value", "Edit", ed), total)


def _document_row(app: Fakturama, kind: str, number: str) -> Row:
    """The row for a document number in Data > Documents, under the 'Orders' or 'Invoices'
    node (the view can open on another category, e.g. Invoices/unpaid, hiding the row)."""
    app.nav("Documents")
    view = app.editor("Documents")
    app.focus()
    app.find(kind, "TreeItem", view).click_input()
    app.park_mouse()
    time.sleep(0.8)
    hits = [r for r in app.rows(view) if cell_eq(r.cells.get("Document", ""), number)]
    if len(hits) != 1:
        raise ManualReview("verify", f"expected 1 row for {number} under {kind}, found {len(hits)}")
    return hits[0]


def verify_document(app: Fakturama, order: OrderData, kind: str, number: str, state: str, day: date | None) -> None:
    """4.5 / 5.5: the document's row has the expected date, Cust.Ref., state and total."""
    c = _document_row(app, kind, number).cells
    expected = {"Cust.Ref.": order.external_ref, "State": state}
    problems = [f"{k} {c.get(k)!r} != {v!r}" for k, v in expected.items() if not cell_eq(c.get(k, ""), v)]
    if day is None or _parse_shown(c.get("Date", "")) != day:
        problems.append(f"Date {c.get('Date')!r} != {day}")
    if abs(parse_number(c.get("Total", "")) - order.total) > TOLERANCE:
        problems.append(f"Total {c.get('Total')!r} != {order.total}")
    if problems:
        raise ManualReview("verify", f"{number}: " + "; ".join(problems))
    app.screenshot(f"verified_{number}")


def verify_invoice_persisted(app: Fakturama, order: OrderData) -> None:
    """5.6: close the Invoice, reopen it from Data > Documents and confirm the stored
    payment method, paid state, payment date and value."""
    number = INVOICE_TAB[1]
    app.close_editor(INVOICE_TAB)
    row = _document_row(app, "Invoices", number)
    ed = _open_row(app, row, number)
    app.dismiss_info()
    paid, method = _payment_controls(app, ed)
    problems = []
    if norm(method.iface_value.CurrentValue) != norm(order.payment.method):
        problems.append(f"payment method {method.iface_value.CurrentValue!r}")
    if bool(paid.get_toggle_state()) != order.payment.paid:
        problems.append(f"paid is {bool(paid.get_toggle_state())}")
    if order.payment.paid:
        at = app.by_label("at", parent=ed).iface_value.CurrentValue
        if _parse_shown(at) != order.payment.date:
            problems.append(f"payment date {at!r} != {order.payment.date}")
        value = parse_number(app.find("Value", "Edit", ed).iface_value.CurrentValue)
        if abs(value - order.total) > TOLERANCE:
            problems.append(f"Value {value} != {order.total}")
    if problems:
        raise ManualReview("verify", f"reopened {number}: " + "; ".join(problems))
    app.screenshot(f"reopened_{number}")


# ---- helpers -------------------------------------------------------------------

def _squash(text: str) -> str:
    """OCR'd text with case and all whitespace removed (OCR adds or drops spaces)."""
    return "".join(norm(text).split())


def _parse_shown(text: str) -> date | None:
    """A date as Fakturama displays it ('Jul 14, 2026'; OCR may drop spaces) -> date."""
    try:
        return datetime.strptime("".join(text.split()), "%b%d,%Y").date()
    except ValueError:
        return None


def choose(app: Fakturama, dlg, row: Row) -> None:
    app.click_at(row.x, row.y)
    app.press("OK", dlg)
