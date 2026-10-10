"""Reading NatTable grids, which UIA exposes as one opaque pane.

1. Screenshot only the grid pane (located via UIA).
2. Find the column separators: the 2px vertical lines drawn in the header band.
3. Re-assemble the columns side by side with wide white gaps, so OCR can't merge
   neighbouring cells ("Marta" | "Klein") and every word lands in exactly one column.
4. OCR once; the header line names the columns, the other lines are rows.

Clicks go to a row's position computed from the pane's live rectangle — no fixed coordinates.
"""

import difflib
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from ..errors import ManualReview

GAP = 40  # white pixels inserted between columns
SCALE = 2  # upscale factor applied before OCR

# Column titles the flow looks up; OCR'd headers are snapped to these ("itate" -> "State").
KNOWN_HEADERS = [
    "No.", "First Name", "Name", "Company", "ZIP", "City", "address type",
    "Item No.", "Description", "Price", "VAT", "Value", "Discount", "Disc. Days", "Net Days",
    "Pos.", "Qty.", "U.Price", "Document", "Date", "Cust.Ref.", "State", "Total", "Printed",
]


def _snap_header(text: str) -> str:
    hit = difflib.get_close_matches(text, KNOWN_HEADERS, n=1, cutoff=0.7)
    return hit[0] if hit else text
_ocr = None


@dataclass
class Row:
    cells: dict[str, str]
    x: int  # screen coordinates of a point inside the row (first named column)
    y: int
    col_x: dict[str, int]  # screen x of each column's centre, for clicking a specific cell


def _header_bottom(gray: np.ndarray) -> int:
    """y of the horizontal line under the header: the first uniform pixel row (no text
    crossing it) whose shade differs from the row above. Brightness alone isn't enough,
    since a selected first row can look like the header. Fallback: 19px (NatTable default)."""
    means, stds = gray[:60].mean(axis=1), gray[:60].std(axis=1)
    for y in range(12, min(31, len(means))):
        if stds[y] < 15 and abs(means[y] - means[y - 1]) > 8:
            return y
    return 19


def _visible_rect(el):
    """The element's rectangle clipped by its ancestors (scrolled editors hide part of a grid)."""
    r = el.rectangle()
    left, top, right, bottom = r.left, r.top, r.right, r.bottom
    p = el.parent()
    while p is not None and p.element_info.control_type != "Window":
        pr = p.rectangle()
        left, top, right, bottom = max(left, pr.left), max(top, pr.top), min(right, pr.right), min(bottom, pr.bottom)
        p = p.parent()
    return left, top, right, bottom


def _separators(gray: np.ndarray, header_bottom: int) -> list[int]:
    """x positions of vertical column lines, looked for in the header band and in the first
    body row (some header styles draw no lines, and empty lists draw none in the body)."""
    xs: set[int] = set()
    for top, bottom in ((1, header_bottom - 1), (header_bottom + 2, header_bottom + 18)):
        band = gray[top:min(bottom, gray.shape[0])]
        if band.shape[0] < 4:
            continue
        # A column line is a 1-2px stroke that differs from the pixels 3px either side.
        # Comparing locally copes with gradients and with highlighted (selected) cells,
        # where lines can be lighter than their surroundings.
        pad = np.pad(band, ((0, 0), (3, 3)), mode="edge")
        neighbours = (pad[:, :-6] + pad[:, 6:]) / 2
        line = (np.abs(band - neighbours) > 20).mean(axis=0)
        xs |= {int(x) for x in np.nonzero(line > 0.8)[0]}
    groups: list[list[int]] = []
    for x in sorted(xs):
        if groups and x - groups[-1][-1] <= 6:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [round(sum(g) / len(g)) for g in groups]


def _ocr_lines(image: Image.Image):
    """OCR -> list of lines, each a list of (text, cx, cy) sorted left to right."""
    global _ocr
    _ocr = _ocr or RapidOCR()
    # UI text is small (about 9px); OCR is noticeably more accurate on a 2x upscale
    big = image.resize((image.width * SCALE, image.height * SCALE), Image.LANCZOS)
    result, _ = _ocr(np.array(big))
    words = []
    for box, text, _score in result or []:
        xs, ys = [p[0] / SCALE for p in box], [p[1] / SCALE for p in box]
        words.append((text.strip(), (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(ys) - min(ys)))
    lines: list[list] = []
    for w in sorted(words, key=lambda w: w[2]):
        if lines and abs(lines[-1][0][2] - w[2]) < w[3] * 0.6:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w[1]) for line in lines]


def read_grid(pane) -> list[Row]:
    """All visible rows of a grid pane as {header: cell text}."""
    rect = pane.rectangle()
    left, top, right, bottom = _visible_rect(pane)
    img = pane.capture_as_image().convert("RGB").crop((left - rect.left, top - rect.top, right - rect.left, bottom - rect.top))
    gray = np.asarray(img.convert("L")).astype(int)
    hb = _header_bottom(gray)
    bounds = [0, *_separators(gray, hb), img.width]
    strips = [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a > 4]

    # columns side by side with wide gaps; remember where each strip landed
    spread = Image.new("RGB", (img.width + GAP * (len(strips) + 1), img.height), "white")
    offsets = []
    x = GAP
    for a, b in strips:
        spread.paste(img.crop((a + 2, 0, b - 1, img.height)), (x, 0))  # trim the separator pixels
        offsets.append(x)
        x += (b - a - 3) + GAP

    def strip_of(cx: float) -> int:
        return max((i for i, off in enumerate(offsets) if cx >= off - GAP / 2), default=0)

    lines = _ocr_lines(spread)
    headers = [""] * len(strips)
    rows = []
    for line in lines:
        cy = sum(w[2] for w in line) / len(line)
        if cy < hb:
            for text, cx, _, _ in line:
                i = strip_of(cx)
                headers[i] = (headers[i] + " " + text).strip()
            headers = [_snap_header(h) if h else h for h in headers]
            continue
        cells: dict[str, str] = {}
        first_x = None
        for text, cx, _, _ in line:
            i = strip_of(cx)
            if not headers[i]:
                continue  # unnamed columns (row number, icons)
            cells[headers[i]] = (cells.get(headers[i], "") + " " + text).strip()
            if first_x is None:
                first_x = strips[i][0] + (cx - offsets[i])
        if cells:
            col_x = {h: int(left + (a + b) / 2) for h, (a, b) in zip(headers, strips) if h}
            rows.append(Row(cells, int(left + first_x), int(top + cy), col_x))
    return rows


def read_stable(pane, tries: int = 5, delay: float = 0.5) -> list[Row]:
    """Re-read until two consecutive reads agree (spec: wait for the list to stabilize)."""
    prev = None
    for _ in range(tries):
        rows = read_grid(pane)
        if prev is not None and [r.cells for r in rows] == [r.cells for r in prev]:
            return rows
        prev = rows
        time.sleep(delay)
    return prev or []


def grid_pane(container, timeout: float = 10):
    """The largest pane with no children other than scrollbars — that's the NatTable canvas.
    Polls, because a freshly opened dialog builds its grid a moment after the window appears."""
    deadline = time.time() + timeout
    while True:
        panes = [
            p for p in container.descendants(control_type="Pane")
            if all(c.element_info.control_type == "ScrollBar" for c in p.children())
            and p.rectangle().width() > 100 and p.rectangle().height() > 50
        ]
        if panes:
            return max(panes, key=lambda p: p.rectangle().width() * p.rectangle().height())
        if time.time() > deadline:
            raise ManualReview("grid", "no grid found in this view/dialog")
        time.sleep(0.3)
