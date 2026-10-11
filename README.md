# Fakturama Image-to-Cash

Turns one order image into a saved Order and a linked, paid-status-correct Invoice in [Fakturama](https://www.fakturama.info/), driven through Microsoft UI Automation.

```
order.png ──► vision LLM (any OpenAI-compatible API) ──► OrderData (pydantic)
                                                            │ rules.check_order: line/total arithmetic
                                                            ▼
                     Fakturama  ◄── UIA (pywinauto): named controls, label lookup, unnamed icons by position in tree
                                ◄── OCR on grid panes only (NatTable is opaque to UIA)
                                            │
                       any doubt ──► ManualReview: screenshot + reason in runs/<ts>/, exit 2
```

Documents: [design](docs/DESIGN.md) (Part 1) · [written answers](docs/WRITTEN_ANSWERS.md) · [recording of a full run](docs/demo.mp4)

## Setup

### Requirements

| | Tested with | Notes |
|---|---|---|
| Windows | 11 (64-bit), display scaling 100% | UI Automation is Windows-only |
| Fakturama | 2.2.0, English interface, dates as `Oct 8, 2026`, amounts with a decimal point (`EUR1,234.50` or `$1,234.50`) | Controls are found by their English names |
| Python | 3.11 | 3.11 or newer |
| Vision LLM | `qwen/qwen3.8-27b` on Groq | Any OpenAI-compatible endpoint whose model accepts images |

No other installs are needed: OCR (RapidOCR) comes as a Python package.

### 1. Install

From the repository folder:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -e .[dev]
```

`dev` adds pytest and is optional.

### 2. Configure the model

```bash
cp .env.example .env
```

Edit `.env`:

```
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<your key>
LLM_MODEL=qwen/qwen3.8-27b
```

`.env.example` lists base URLs for OpenAI, OpenRouter and Ollama. `.env` is git-ignored.

### 3. Prepare Fakturama

1. Install Fakturama from <https://www.fakturama.info/download/> and start it once, so it creates its workspace and default data.
2. Leave its main window open. It may be minimized or behind other windows; the tool brings it to the front and maximizes it.
3. Close any editor tabs with unsaved changes.
4. Optional, to show euros like the sample order: **File > Preferences > General**, set *Currency locale* to **Ireland** and choose **ISO code**. Amounts then read `EUR678.30`. Any currency locale that writes decimals with a point works; one that uses a decimal comma (for example Germany) does not.

A new installation is all the flow needs: it creates the payment method, Debtor, VAT rate and Products that are missing.

### 4. Check the installation

```bash
.venv/Scripts/python -m pytest -q
```

Expect `13 passed`. These test the spec rules and need neither Fakturama nor the LLM.

## Run

Extract only (no UI; prints and saves the validated data):

```bash
.venv/Scripts/python -m i2c extract samples/order.png
```

Full flow, image to saved Invoice (about 4 minutes for the sample):

```bash
.venv/Scripts/python -m i2c run samples/order.png
```

Replay a saved extraction without calling the LLM:

```bash
.venv/Scripts/python -m i2c run runs/<timestamp>/extracted.json
```

**While it runs, leave the mouse and keyboard alone and keep the screen unlocked.** The tool uses real clicks and keystrokes, and it uses the clipboard for search boxes.

### What a run produces

Everything goes to `runs/<timestamp>/`:

| File | Content |
|---|---|
| `extracted.json` | The data read from the image, after validation |
| `HHMMSS_<step>.png` | A screenshot after each step (Order opened, Debtor, each Product and line, saved documents, reopened Invoice) |
| `manual_review.txt` + `…_manual_review_<step>.png` | Only if the run stopped: the reason and the screen at that moment |
| `information.txt` | Only if Fakturama showed an "already ordered n times" notice: its text |

The console prints each spec step as it starts.

| Exit code | Meaning |
|---|---|
| 0 | Order and Invoice saved and verified |
| 2 | Stopped for manual review; nothing further was changed |

### After a stopped run

The stop leaves Fakturama as it was at that moment, usually with an unsaved editor open. To close all open editors **without saving**:

```bash
.venv/Scripts/python tools/discard_editors.py
```

Records saved before the stop (for example a new Debtor) stay, and the next run finds and reuses them. Running the same image again after a **successful** run creates a second Order; there is no duplicate check.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `[unexpected] KeyError: 'LLM_…'` | `.env` is missing or incomplete (step 2) |
| `[unexpected] BadRequestError …` from the model | Most likely the model does not accept images; use a vision model |
| `[extract] …` manual review | The model's numbers did not add up twice; check the image, or try another model |
| `[unexpected] TimeoutError`, or `[ui] … not found` on the first control | Fakturama is not running, or its interface is not in English |
| `[ui] could not focus …` or typed text in the wrong place | The mouse or keyboard was used during the run; discard editors and run again |
| Dates or amounts fail to match | Fakturama shows dates or decimals in another format (see Requirements) |

## Layout

| File | Role |
|---|---|
| `src/i2c/models.py` | Extracted data schema |
| `src/i2c/rules.py` | Spec rules: arithmetic checks, gross price, VAT naming, payment-code mapping, exact/truncated cell matching |
| `src/i2c/llm.py`, `extract.py` | Provider-agnostic LLM call (JSON mode + image), validate, one retry with error feedback |
| `src/i2c/ui/app.py` | UIA helpers: find by name/label, verified focus, set_text/number/date/combo/check, save, maximize/restore |
| `src/i2c/ui/grid.py` | OCR-based reading of NatTable grids: column lines from pixels, columns spread apart before OCR |
| `src/i2c/flow.py` | The spec's Order-first flow, one function per step |
| `tools/dump_uia.py` | Dev: print the UIA tree of an editor or dialog |
| `tools/discard_editors.py` | Dev: close all editors without saving (reset after an aborted run) |

## Status

Verified against Fakturama 2.2.0 with `samples/order.png` (the task's sample) in one uninterrupted
run on a new installation (about 4 minutes, exit code 0). Result in Data > Documents:
**INV000001** paid, EUR 678.30, linked to **PO000001** (Jul 14, 2026, WEB-2026-0714-A17, open, EUR 678.30).

| Spec step | State |
|---|---|
| 1 Extract + open Order (date, Cust.Ref., Net, With VAT) | ✅ |
| 2.1-2.4 Select existing Debtor; Invoice **and** Delivery address confirmed | ✅ |
| 2.5-2.13 Create Debtor (separate delivery address, or both roles on one), alias, Net, payment | ✅ |
| 2.10 Payment method: reuse only if description, zero terms and payment code match; else create | ✅ |
| 3.2-3.3, 3.12 Product select by exact SKU | ✅ |
| 3.4-3.6 VAT: reuse only if name, value and code S match (row opened to check); else create | ✅ |
| 3.7-3.11 Product create (gross price without line discount) | ✅ |
| 3.13-3.16 Item line Qty, U.Price, VAT, Discount, line Price | ✅ |
| 4.1-4.3 Before saving: every line, overall discount and shipping (from the image, else 0% / free), addresses, Total Net / VAT / Total | ✅ |
| 4.4-4.7 Save, Documents row check, follow-up Invoice | ✅ |
| 5.1 Invoice: Cust.Ref., addresses, Order Date, VAT mode, item lines, totals | ✅ |
| 5.2-5.3 Payment method; PAID -> tick, date, full value; not PAID -> left clear | ✅ |
| 5.4-5.5 Save; Invoice row state and total; source Order still open | ✅ |
| 5.6 Invoice closed, reopened from Documents; stored method, paid, date, value confirmed | ✅ |

Branches exercised on the real app, beyond the sample (replayed from JSON, see below):
- existing Debtor / Products / VAT / payment method (reuse paths),
- **not PAID** (paid left clear, no date or value, list state `unpaid`),
- **identical billing and delivery address** (one address with both roles),
- a second payment method (**Credit Card** -> code "Credit card") and VAT rate (7%), a 5% line discount,
- an **order-level discount (5%) and shipping charge (10.00)**, on the Order and the Invoice.

Not exercised: the "ambiguous / conflicting match" stops against real duplicate records (the rule
itself is unit-tested), and any image other than the sample. In particular, order-level discount and
shipping were tested by replaying edited data, not by extracting them from an image that shows them.

## Deviation from the spec
- **Payment method before the Debtor editor.** The Debtor's Payment dropdown is filled when the editor
  opens and doesn't pick up a method created meanwhile, so the method is resolved/created first
  (the Order tab stays open throughout, as required).

## Fakturama quirks the code handles
- SWT ignores UIA-only changes to combos, checkboxes and date fields → real keystrokes/clicks.
- Date fields edit one segment at a time: click the month segment, type the digits slowly.
- Data > Documents can open on a category (e.g. Invoices/unpaid) → the Orders / Invoices node is selected first.
- "... has already ordered n times" popup for known customers → dismissed, text kept in the run folder.
- The follow-up Invoice copies the overall discount but not a shipping amount typed on the Order → the amount is entered on the Invoice too, then the totals are checked.
- Grids are read from the screen → Fakturama is brought to the front before every read.
- Multi-line fields (Company, Description) need a real keystroke after UIA SetValue to register.
- Eclipse may keep keyboard focus in a list view → every typing step verifies focus first.
- The product selector auto-picks when the search leaves one match → search terms are pasted whole,
  and the new line's SKU is verified.
- Grid tooltips, highlighted rows and partially hidden grids → mouse parked, local-contrast column
  detection, capture clipped to the visible area, Order editor maximized while editing lines.

## Known limitations
- Dates must display as `Oct 8, 2026` and amounts with a decimal point; the currency symbol or code and thousands separators are ignored. A decimal-comma locale is not supported.
- Grid reading covers visible rows only (about 12 item lines with the editor maximized); no scrolling.
- No duplicate check on Cust.Ref.: running the same image twice creates two Orders.
- `search()` uses the clipboard (overwrites it).
- OCR'd grid cells are matched exactly (spaces ignored); a misread character means manual review, not a wrong match.
- The Groq vision model used in testing is a preview model; any OpenAI-compatible vision model can replace it via `.env`.
