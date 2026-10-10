"""Thin UIA layer over Fakturama. Controls are located by name, role and label — never by fixed coordinates.

Findings from inspecting Fakturama (SWT) with UIA:
- toolbar / view buttons are named by their tooltip ("Create: New Order", "Create a new tax rate")
- left navigation entries are named Text elements ("Documents", "VATs", "New Contact")
- most form fields are named after their label; some (No., Date, First/Last Name, ZIP/City) are
  unnamed and found via the label to their left
- only the active editor's and active sub-tab's controls are in the tree, so lookups are scoped
  to an editor's Tab element to avoid same-named fields in other editors
- text fields accept UIA SetValue; combos and checkboxes need real input (SWT ignores UIA-only changes)
- grids (NatTable) are opaque to UIA -> see grid.py
- real input goes to the foreground window, so we focus Fakturama first
"""

import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import win32clipboard
from pywinauto import Desktop, mouse
from pywinauto.uia_defines import IUIA
from pywinauto.uia_element_info import UIAElementInfo

from ..errors import ManualReview
from ..rules import parse_number

TIMEOUT = 10


def _keys(text: str) -> str:
    """Escape pywinauto's type_keys specials so text is typed literally."""
    return "".join("{%s}" % c if c in "+^%~(){}[]" else c for c in text)


def _has_focus(el) -> bool:
    """True if the keyboard focus is on `el` or inside it (a combo box focuses its inner part)."""
    focused = UIAElementInfo(IUIA().iuia.GetFocusedElement())
    r, f = el.rectangle(), focused.rectangle
    return focused == el.element_info or (
        f.left >= r.left and f.top >= r.top and f.right <= r.right and f.bottom <= r.bottom and f.width() > 0
    )


def _wait_focus(el, timeout: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _has_focus(el):
            return True
        time.sleep(0.1)
    return False


class Fakturama:
    def __init__(self, run_dir: Path):
        self.win = Desktop(backend="uia").window(class_name="SWT_Window0", title_re=r"Fakturama.*")
        self.win.wait("exists", timeout=TIMEOUT)  # not "ready": a modal dialog disables it
        self.fullscreen()
        self.run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)

    def step(self, text: str) -> None:
        """Log the spec step being worked on."""
        print(text, flush=True)

    def fullscreen(self) -> None:
        """Keep the Fakturama window maximized: the most room for editors and grids, and the
        same layout on every run whatever size the window was left at."""
        w = self.win.wrapper_object()
        if not w.is_maximized():
            w.maximize()  # also brings a minimized window back
            time.sleep(0.8)

    # ---- finding -------------------------------------------------------------

    def find(self, name: str, control_type: str, parent=None, timeout: float = TIMEOUT):
        """First descendant with this exact name and role, polling until it appears."""
        root = parent if parent is not None else self.win
        deadline = time.time() + timeout
        while True:
            try:
                hits = root.descendants(title=name, control_type=control_type)
                if hits:
                    return hits[0]
            except Exception:
                pass  # tree changed mid-walk; retry
            if time.time() > deadline:
                raise ManualReview("ui", f"{control_type} {name!r} not found")
            time.sleep(0.25)

    def by_label(self, label: str, control_type: str = "Edit", parent=None, index: int = 0):
        """The index-th control of `control_type` to the right of a label, on the same row."""
        lbl = self.find(label, "Text", parent)
        lr = lbl.rectangle()
        scope = lbl.parent()
        for _ in range(3):  # widen the search scope a few levels if needed
            candidates = sorted(
                (c for c in scope.descendants(control_type=control_type)
                 if c.rectangle().left >= lr.right and c.rectangle().top <= lr.bottom and c.rectangle().bottom >= lr.top),
                key=lambda c: c.rectangle().left,
            )
            if len(candidates) > index:
                return candidates[index]
            scope = scope.parent()
        raise ManualReview("ui", f"no {control_type} #{index} next to label {label!r}")

    def icons_near(self, label: str, parent=None) -> list:
        """Unnamed icon buttons grouped with a label (e.g. 'Addresses'), top to bottom."""
        lbl = self.find(label, "Text", parent)
        imgs = [c for c in lbl.parent().children() if c.element_info.control_type == "Image"]
        return sorted(imgs, key=lambda c: c.rectangle().top)

    def dialog(self, title: str, timeout: float = TIMEOUT):
        return self.find(title, "Window", timeout=timeout)

    def dismiss_info(self, wait: float = 1.5) -> str | None:
        """Close Fakturama's 'Information' popup if it shows up (e.g. "... has already ordered
        1 times in the past" when a known customer is put on a document). Its text is kept in
        the run folder as evidence. Returns the text, or None if no popup appeared."""
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.is_open("Information"):
                dlg = self.dialog("Information")
                text = " ".join(t.window_text() for t in dlg.descendants(control_type="Text"))
                with open(self.run_dir / "information.txt", "a", encoding="utf-8") as f:
                    f.write(text + "\n\n")
                self.press("OK", dlg)
                time.sleep(0.5)
                return text
            time.sleep(0.2)
        return None

    def close_editor(self, title: str | tuple) -> None:
        """Close a saved editor tab (Ctrl+F4). An unsaved one would ask to save: stop instead."""
        self.activate_tab(title)
        self.key("^{F4}")
        time.sleep(1)
        if self.is_open("Save Parts"):
            raise ManualReview("ui", f"editor {title!r} has unsaved changes, not closing it")

    def is_open(self, title: str) -> bool:
        """Is a dialog with this title still open (without waiting)?"""
        return any(c.window_text() == title for c in self.win.wrapper_object().children()
                   if c.element_info.control_type == "Window")

    def editor(self, title: str | tuple, timeout: float = TIMEOUT):
        """The open editor/view (a Tab element) whose title contains `title` (or any of several)."""
        titles = (title,) if isinstance(title, str) else title
        deadline = time.time() + timeout
        while time.time() < deadline:
            for t in self.win.descendants(control_type="Tab"):
                if any(x in t.window_text() for x in titles):
                    return t
            time.sleep(0.25)
        raise ManualReview("ui", f"editor {title!r} not open")

    def activate_tab(self, title: str | tuple) -> None:
        """Bring an open editor tab (e.g. the in-progress Order) to the front."""
        titles = (title,) if isinstance(title, str) else title
        for t in self.win.descendants(control_type="TabItem"):
            if any(x in t.window_text() for x in titles):
                self.focus()
                t.click_input()
                self.park_mouse()
                time.sleep(0.3)
                return
        raise ManualReview("ui", f"editor tab {title!r} not open")

    # ---- acting --------------------------------------------------------------

    def focus(self) -> None:
        """Real input (clicks, keys, screenshots) goes to the foreground window, so make sure it's us."""
        self.fullscreen()
        w = self.win.wrapper_object()
        if not w.is_active():
            w.set_focus()
            time.sleep(0.3)

    def park_mouse(self) -> None:
        """Move the pointer to the title bar: a pointer resting on an icon shows a tooltip
        that covers grids and gets OCR'd as a row."""
        r = self.win.wrapper_object().rectangle()
        mouse.move(coords=((r.left + r.right) // 2, r.top + 8))

    def click(self, el) -> None:
        try:
            el.invoke()
        except Exception:
            self.focus()
            el.click_input()  # coordinates come from the element's live UIA rectangle
            self.park_mouse()

    def click_at(self, x: int, y: int, double: bool = False) -> None:
        """Click a point computed from a live UIA rectangle (grid rows)."""
        self.focus()
        (mouse.double_click if double else mouse.click)(coords=(x, y))
        self.park_mouse()

    def press(self, name: str, parent=None) -> None:
        self.click(self.find(name, "Button", parent))

    def _restore_buttons(self) -> list:
        """Restore buttons of maximized stacks (not the window's own title-bar button)."""
        return [b for b in self.win.descendants(title="Restore", control_type="Button")
                if b.parent().element_info.control_type != "TitleBar"]

    def maximize(self, title: str | tuple) -> None:
        """Give an editor the whole window (its stack's Maximize button) so its Items grid shows
        more than one row. This hides the list views; nav() restores the layout first.
        State is read from the screen: a maximized stack offers Restore instead of Maximize."""
        if self._restore_buttons():
            return
        self.click(self.find("Maximize", "Button", self.editor(title)))
        time.sleep(0.8)

    def restore(self) -> None:
        buttons = self._restore_buttons()
        for b in buttons:
            self.click(b)
        if buttons:
            time.sleep(0.8)

    def nav(self, name: str) -> None:
        """Left navigation panel entry, e.g. 'VATs', 'New Contact'."""
        self.restore()
        el = self.find(name, "Text")
        self.focus()
        el.click_input()
        self.park_mouse()
        time.sleep(0.5)

    def focus_on(self, el) -> None:
        """Give `el` keyboard focus and make sure it really has it before anything is typed.
        Eclipse can refuse UIA focus requests while another part (a list view) is active;
        a real click on a text field fixes that. Keys must never land somewhere unchecked."""
        self.focus()
        for _ in range(3):  # re-ask: a Tab still being processed can move focus away again
            el.set_focus()
            if _wait_focus(el, 0.5):
                return
        # Fallback: a real click (always moves focus). On a combo it opens the dropdown;
        # Escape closes it again without changing the selection.
        el.click_input()
        if el.element_info.control_type == "ComboBox":
            time.sleep(0.3)
            el.type_keys("{ESC}", set_foreground=False)
        self.park_mouse()
        if not _wait_focus(el):
            focused = UIAElementInfo(IUIA().iuia.GetFocusedElement())
            raise ManualReview("ui", f"could not focus {el.element_info.control_type} {el.window_text()!r} at "
                                     f"{el.rectangle()}; focus is on {focused.control_type} {focused.name!r} at {focused.rectangle}")

    def set_text(self, el, value: str) -> None:
        """SetValue (fast, any characters), then a real no-op keystroke: multi-line fields
        (Company, Description) only notify Fakturama of changes made by real key events."""
        el.iface_value.SetValue(value)
        self.focus_on(el)
        el.type_keys("^{END}{SPACE}{BACKSPACE}", set_foreground=False)
        time.sleep(0.2)
        if el.iface_value.CurrentValue != value:
            raise ManualReview("ui", f"field kept {el.iface_value.CurrentValue!r}, expected {value!r}")

    def search(self, parent, text: str) -> None:
        """Fill a list's 'Search:' box by pasting the whole term in one go: typing letter by
        letter can pass through a prefix that matches a single wrong entry, and the product
        selector auto-picks single matches. The box doesn't report its value to UIA, so callers
        verify through the filtered grid instead. (Overwrites the clipboard.)"""
        box = self.by_label("Search:", parent=parent)
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        self.focus_on(box)
        box.type_keys("^a^v", set_foreground=False)
        time.sleep(0.8)

    def type_into(self, el, text: str) -> str:
        """Type like a user and Tab out so Fakturama re-formats the field; returns the shown value."""
        self.focus_on(el)
        el.type_keys("^a" + _keys(text) + "{TAB}", set_foreground=False)
        time.sleep(0.3)
        return el.iface_value.CurrentValue

    def set_number(self, el, value: Decimal) -> None:
        """Money / percent / quantity fields, e.g. '$297.50' or '19%'. Compared numerically."""
        for _ in range(2):  # retry once: early keystrokes can miss the field
            shown = self.type_into(el, f"{value:f}")
            if parse_number(shown) == value:
                return
        raise ManualReview("ui", f"number field shows {shown!r}, expected {value}")

    def set_date(self, el, d: date) -> None:
        """Date fields edit segment by segment and ignore SetValue. Click the field's left edge
        (the month segment: a click elsewhere starts in whichever segment it hits), type
        MMDDYYYY at a slow pace (fast typing drops digits while the field auto-advances),
        then compare what the field shows (e.g. 'Jul 14, 2026' or 'Aug 5, 2026')."""
        for _ in range(3):
            self.focus()
            el.click_input(coords=(6, el.rectangle().height() // 2))
            self.park_mouse()
            if not _wait_focus(el):
                continue
            el.type_keys(f"{d:%m%d%Y}{{TAB}}", pause=0.15, set_foreground=False)
            time.sleep(0.4)
            try:
                if datetime.strptime("".join(el.iface_value.CurrentValue.split()), "%b%d,%Y").date() == d:
                    return
            except ValueError:
                pass
        raise ManualReview("ui", f"date field shows {el.iface_value.CurrentValue!r}, expected {d}")

    def set_combo(self, el, value: str) -> None:
        """Select by typing (type-ahead), else walk the list with arrow keys.
        SWT ignores UIA-only selection, but real keys fire its events."""
        self.focus_on(el)
        el.type_keys(_keys(value), with_spaces=True, set_foreground=False)
        if el.iface_value.CurrentValue.strip() == value:
            return
        el.type_keys("{HOME}", set_foreground=False)
        seen = None
        while el.iface_value.CurrentValue.strip() != value:
            if el.iface_value.CurrentValue == seen:  # bottom of the list
                raise ManualReview("ui", f"combo has no option {value!r}")
            seen = el.iface_value.CurrentValue
            el.type_keys("{DOWN}", set_foreground=False)

    def set_check(self, el, on: bool = True) -> None:
        if bool(el.get_toggle_state()) != on:
            self.focus()
            el.click_input()
            time.sleep(0.2)
        if bool(el.get_toggle_state()) != on:
            raise ManualReview("ui", f"checkbox {el.window_text()!r} did not change")

    def key(self, keys: str) -> None:
        self.focus()
        self.win.type_keys(keys)

    def save(self) -> None:
        """Toolbar Save once, then wait until it greys out (= the active editor is no longer dirty)."""
        btn = self.find("Save the current contents", "Button")
        if not btn.is_enabled():
            raise ManualReview("save", "nothing to save: the editor has no changes")
        self.click(btn)
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            if not btn.is_enabled():
                return
            time.sleep(0.3)
        raise ManualReview("save", "editor still unsaved after Save (validation error?)")

    def screenshot(self, name: str) -> Path:
        self.focus()
        path = self.run_dir / f"{datetime.now():%H%M%S}_{name}.png"
        self.win.wrapper_object().capture_as_image().save(path)
        return path
